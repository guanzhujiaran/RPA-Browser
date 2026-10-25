"""
工作流定时调度（调度外壳的调度侧）

把「启用 + cron 触发 + 已指定浏览器」的工作流注册进全局 apscheduler，
并在启动时按库重建全部任务。

约定：
    - 任务ID 固定为 ``workflow:{workflow_id}``，便于幂等地增量同步
    - cron 表达式为 5 段（分 时 日 月 周），与 `SchedulerManager.add_cron_job` 一致
    - 调度回调内的异常全部收敛，绝不打断调度器线程
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from app.models.database.workflow.models import UserWorkflow
from app.scheduler_manager import scheduler_manager_ist
from app.services.execution.crud_service import workflow_crud_svr
from app.services.execution.workflow_runner import launch_workflow_run
from app.models.database.workflow.models import WorkflowRunTriggerEnum

JOB_ID_PREFIX = "workflow:"


def job_id_of(workflow_id: int) -> str:
    """工作流对应的调度任务ID（APScheduler job_id 为字符串标识）"""
    return f"{JOB_ID_PREFIX}{workflow_id}"


def extract_cron(workflow: UserWorkflow) -> str | None:
    """从工作流触发配置中提取 cron 表达式"""
    config: Dict[str, Any] = workflow.trigger_config or {}
    cron = config.get("cron")
    if isinstance(cron, str) and cron.strip():
        return cron.strip()
    return None


def validate_cron(cron_expression: str) -> bool:
    """校验 5 段 cron 表达式是否可解析"""
    if not cron_expression:
        return False
    try:
        build_cron_trigger(cron_expression)
        return True
    except Exception:  # noqa: BLE001
        return False


def build_cron_trigger(cron_expression: str) -> CronTrigger:
    """按 5 段表达式构造 CronTrigger（与 SchedulerManager.add_cron_job 语义一致）"""
    parts = cron_expression.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {cron_expression}")
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week
    )


def compute_next_run(
    cron_expression: str, now: datetime | None = None
) -> datetime | None:
    """推导下次触发时间（仅用于展示 / 排障，失败返回 None）

    注意：apscheduler 返回带时区的 datetime，而库内其余时间字段均为本地 naive，
    这里统一转换为本地 naive，避免存储与展示口径不一致。
    """
    try:
        trigger = build_cron_trigger(cron_expression)
        next_run = trigger.get_next_fire_time(None, now or datetime.now())
        if next_run is None:
            return None
        if next_run.tzinfo is not None:
            next_run = next_run.astimezone().replace(tzinfo=None)
        return next_run
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[WorkflowScheduler] 推导下次运行时间失败: {cron_expression}, {exc}"
        )
        return None


def is_schedulable(workflow: UserWorkflow) -> bool:
    """是否应注册定时任务：启用 + cron 触发 + cron 可解析 + 已指定浏览器"""
    if not workflow.is_enabled:
        return False
    if workflow.trigger_type != "cron":
        return False
    if workflow.browser_id is None:
        return False
    cron = extract_cron(workflow)
    return bool(cron) and validate_cron(cron or "")


def remove_workflow_job(workflow_id: str) -> bool:
    """移除工作流的调度任务（不存在时静默成功）"""
    job_id = job_id_of(workflow_id)
    try:
        scheduler_manager_ist.remove_job(job_id)
        return True
    except Exception:  # noqa: BLE001 - 任务不存在 / 调度器未启动
        logger.debug(f"[WorkflowScheduler] 无需移除任务: {job_id}")
        return False


async def sync_workflow_job(workflow: UserWorkflow) -> bool:
    """按工作流当前配置同步调度任务（注册或移除），并刷新 next_run_at

    Returns:
        True 表示当前处于「已注册定时任务」状态
    """
    if not is_schedulable(workflow):
        remove_workflow_job(workflow.workflow_id)
        if workflow.next_run_at is not None:
            await workflow_crud_svr.update_run_state(id=workflow.id, next_run_at=None)
        return False

    cron = extract_cron(workflow) or ""
    next_run = compute_next_run(cron)
    try:
        scheduler_manager_ist.add_cron_job(
            func=run_workflow_job,
            cron_expression=cron,
            id=job_id_of(workflow.workflow_id),
            name=f"工作流: {workflow.name}",
            replace_existing=True,
            kwargs={"workflow_id": workflow.workflow_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            f"[WorkflowScheduler] 注册定时任务失败: {workflow.workflow_id}, {exc}"
        )
        return False

    if next_run != workflow.next_run_at:
        await workflow_crud_svr.update_run_state(id=workflow.id, next_run_at=next_run)
    return True


async def disable_workflow_schedule(workflow: UserWorkflow) -> bool:
    """关闭工作流的定期执行（停用 + 移除调度任务）。

    用于「停止工作流」：用户点停止后，除立刻中断本次运行外，还要让随后的 cron
    触发不再拉起新运行（见 ``docs/工作流停止计划书.md`` §3.2）。

    为什么改 ``is_enabled`` 而不是只 ``remove_job``：调度任务在服务启动
    （``register_all_workflow_jobs``）与任意工作流 ``update`` 时都会按 ``is_enabled``
    重新注册，只删 job 会在重启 / 保存后复活。

    Returns:
        True 表示本次确实关闭了定时执行；False 表示该工作流本来就没有定时执行。
    """
    if not workflow.is_enabled or workflow.trigger_type != "cron":
        return False
    if not extract_cron(workflow):
        return False

    updated = await workflow_crud_svr.update(id=workflow.id, is_enabled=False)
    if updated is None:
        logger.warning(
            f"[WorkflowScheduler] 停用工作流失败，定时任务保持原状: {workflow.workflow_id}"
        )
        return False

    await sync_workflow_job(updated)
    logger.info(
        f"[WorkflowScheduler] 用户停止工作流，已关闭定期执行: {workflow.workflow_id}"
    )
    return True


async def register_all_workflow_jobs() -> int:
    """启动时按库重建全部工作流定时任务

    返回成功注册的任务数。DB / 调度器异常只告警，不阻塞服务启动。
    """
    try:
        workflows = await workflow_crud_svr.list_scheduling()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[WorkflowScheduler] 读取待调度工作流失败，跳过注册: {exc}")
        return 0

    registered = 0
    for workflow in workflows:
        if await sync_workflow_job(workflow):
            registered += 1
    logger.info(f"✅ 工作流定时任务注册完成: {registered}/{len(workflows)} 个已注册")
    return registered


async def run_workflow_job(workflow_id: str) -> None:
    """调度回调：执行一次工作流

    apscheduler 回调，禁止抛出异常；运行结束后刷新 next_run_at。
    """
    try:
        workflow = await workflow_crud_svr.get_by_workflow_id(workflow_id)
        if not workflow:
            logger.warning(f"[WorkflowScheduler] 工作流不存在，移除任务: {workflow_id}")
            remove_workflow_job(workflow_id)
            return

        if not workflow.is_enabled:
            logger.info(
                f"[WorkflowScheduler] 工作流已停用，跳过本次执行: {workflow_id}"
            )
            return

        # 异步启动：立即返回 run_id，执行在后台 task 中进行，不阻塞调度线程
        run_id = await launch_workflow_run(
            workflow, trigger_source=WorkflowRunTriggerEnum.SCHEDULE
        )
        logger.info(f"[WorkflowScheduler] 已启动定时运行: run={run_id}")

        # 运行后刷新下次触发时间（跨天 / 表达式变更场景）
        cron = extract_cron(workflow)
        if cron:
            await workflow_crud_svr.update_run_state(
                id=workflow.id, next_run_at=compute_next_run(cron)
            )
    except Exception as exc:  # noqa: BLE001 - 调度回调必须吞异常
        logger.exception(
            f"[WorkflowScheduler] 定时执行工作流异常: {workflow_id}, {exc}"
        )


__all__ = [
    "JOB_ID_PREFIX",
    "job_id_of",
    "extract_cron",
    "validate_cron",
    "build_cron_trigger",
    "compute_next_run",
    "is_schedulable",
    "remove_workflow_job",
    "sync_workflow_job",
    "disable_workflow_schedule",
    "register_all_workflow_jobs",
    "run_workflow_job",
]
