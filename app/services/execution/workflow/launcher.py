"""工作流异步启动 / 任务收尾 / 断点恢复

从 ``workflow_runner`` 拆出：

- :func:`launch_workflow_run`：Docker ``docker run`` 语义——立即返回 run_id，
  执行在后台 ``asyncio.Task`` 中进行，支持多浏览器（并发 / 串行）；
- :func:`_on_run_task_done` / :func:`_finalize_run_if_needed`：task 收尾与幂等补收口；
- :func:`resume_run_record` / :func:`resume_interrupted_runs`：重启断点恢复。

恢复语义为**至少一次**：从 ``checkpoint_step`` 之后继续，当前步骤重跑；
循环内已处理资源由 WorkflowResourceRecord（success/skipped）跳过。
"""

from __future__ import annotations

import asyncio
from functools import partial

from loguru import logger

from app.config import settings
from app.models.database.workflow.models import (
    UserWorkflow,
    WorkflowRunRecord,
    WorkflowRunStatusEnum,
    WorkflowRunTriggerEnum,
)
from app.services.execution.crud_service import (
    workflow_crud_svr,
    workflow_run_crud_svr,
)
from app.services.execution.run_tasks import workflow_run_tasks
from app.services.execution.workflow.run_workflow import run_workflow


def launch_workflow_run(
    workflow: UserWorkflow,
    *,
    trigger_source: WorkflowRunTriggerEnum = WorkflowRunTriggerEnum.MANUAL,
    browser_id: int | None = None,
    auth_headers: dict[str, str] | None = None,
    resume_from: WorkflowRunRecord | None = None,
) -> str:
    """异步启动一次工作流运行，立即返回 run_id

    借鉴 Docker ``docker run``：命令立即返回容器 ID，容器在后台运行；此处沿用同一
    语义——HTTP 路由与调度器都调用本函数，``run_id`` 立即返回，真正的执行在后台
    ``asyncio.Task`` 中进行，并由 :func:`_on_run_task_done` 统一收尾。

    必须在有运行中事件循环的异步上下文中调用（路由协程 / 调度回调内均可）。

    Args:
        resume_from: 断点恢复来源（已中断的运行）；None 表示全新运行。

    Returns:
        预生成的 ``run_id``（与最终落库的运行记录一致，可立即用于查询 / 停止）。
    """
    # ── 执行目标浏览器：显式传参 > 断点恢复所在浏览器 > 工作流配置的浏览器列表 ──
    def _spawn(run_id: str, target_browser_id: int | None) -> asyncio.Task:
        task = asyncio.create_task(
            run_workflow(
                workflow,
                trigger_source=trigger_source,
                browser_id=target_browser_id,
                auth_headers=auth_headers,
                run_id=run_id,
                resume_from=resume_from,
            ),
            name=f"workflow-run:{run_id}",
        )
        workflow_run_tasks.register(run_id, task)
        task.add_done_callback(partial(_on_run_task_done, run_id))
        return task

    run_id = workflow_run_crud_svr.new_run_id()

    if browser_id is not None:
        targets: list[int] = [int(browser_id)]
    elif resume_from is not None:
        targets = [int(resume_from.browser_id or 0)]
    else:
        targets = [int(b) for b in (workflow.browser_ids or []) if b] or (
            [int(workflow.browser_id)] if workflow.browser_id else []
        )

    # 单浏览器：沿用原有语义
    if len(targets) <= 1:
        task = _spawn(run_id, targets[0] if targets else None)
        logger.info(
            f"[WorkflowRunner] 已启动后台运行: workflow={workflow.workflow_id}, "
            f"trigger={trigger_source}, run={run_id}"
        )
        return run_id

    # ── 多浏览器：每个浏览器一次独立运行；并发开关决定是否同时跑 ──
    run_ids = [run_id] + [workflow_run_crud_svr.new_run_id() for _ in targets[1:]]
    logger.info(
        f"[WorkflowRunner] 多浏览器运行: workflow={workflow.workflow_id}, "
        f"browsers={targets}, 并发={bool(workflow.concurrent_exec)}"
    )

    if workflow.concurrent_exec:
        for rid, bid in zip(run_ids, targets):
            _spawn(rid, bid)
        return run_id

    async def run_sequentially() -> None:
        """按浏览器列表顺序逐个执行：前一个浏览器运行结束再启动下一个。"""
        for rid, bid in zip(run_ids, targets):
            sub_task = _spawn(rid, bid)
            try:
                await sub_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单个浏览器失败不中断后续浏览器
                logger.warning(
                    f"[WorkflowRunner] 浏览器 {bid} 运行失败（继续下一个浏览器）: {exc}"
                )

    asyncio.create_task(run_sequentially(), name=f"workflow-run-seq:{run_id}")
    return run_id


def _on_run_task_done(run_id: str, task: asyncio.Task) -> None:
    """运行 task 结束回调：清理句柄 + 兜底幂等收口

    ``done_callback`` 内不能 await，故这里同步清理句柄，并把「幂等收口」丢回事件
    循环执行：正常路径下 ``run_workflow`` 已写终态，收口为空操作；异常路径（task 在
    写终态前被取消）则补收口为 INTERRUPTED，避免运行记录永久停留在 RUNNING。
    """
    workflow_run_tasks.clear(run_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 事件循环已关闭（进程关停）：无循环可用，交由启动恢复 (interrupt_stale_running) 兜底
        return
    loop.create_task(_finalize_run_if_needed(run_id))


async def _finalize_run_if_needed(run_id: str) -> None:
    """幂等收口：task 结束后若记录仍为 RUNNING，补收口为 INTERRUPTED

    正常情况下运行体已调用 ``finish()`` 写入终态，此处查到的状态非 RUNNING，直接
    返回（幂等，不覆盖已终结状态）。
    """
    try:
        record = await workflow_run_crud_svr.get_by_run_id(run_id)
        if record is None or record.status != WorkflowRunStatusEnum.RUNNING:
            return
        await workflow_run_crud_svr.interrupt_stale_running(
            reason="运行任务已结束但未正常收口，自动收口为中断",
            run_ids=[run_id],
        )
        logger.warning(
            f"[WorkflowRunner] 运行任务结束但未收口，已补收口为中断: run={run_id}"
        )
    except Exception as exc:  # noqa: BLE001 - 兜底收口失败不打断事件循环
        logger.warning(f"[WorkflowRunner] 补收口运行失败: run={run_id}, {exc}")


# ───────────────────────── 重启断点恢复 ─────────────────────────


async def resume_run_record(
    run: WorkflowRunRecord, *, require_auto_switch: bool = False
) -> str | None:
    """按一条已中断的运行恢复执行，返回新 run_id（不可恢复时返回 None）

    Args:
        run: 已收口为 INTERRUPTED 的运行记录。
        require_auto_switch: True=仅恢复开启了 ``resume_on_restart`` 的工作流
            （启动自动恢复）；False=用户手动「继续运行」，不受开关约束。

    Returns:
        新运行的 run_id；不满足条件 / 同浏览器已有运行 / 次数超限时为 None。
    """
    if run.status != WorkflowRunStatusEnum.INTERRUPTED or not run.resumable:
        return None
    if int(run.resume_count or 0) >= settings.workflow_resume_max_count:
        # 连续恢复太多次（通常是反复崩溃）：消费标记，避免每次启动都重试
        await workflow_run_crud_svr.mark_resume_consumed(run.run_id)
        logger.warning(
            f"[WorkflowRunner] 恢复次数超限，放弃恢复: run={run.run_id}, "
            f"resume_count={run.resume_count}"
        )
        return None

    workflow = await workflow_crud_svr.get_by_workflow_id(run.workflow_id)
    if workflow is None or not workflow.is_enabled:
        return None
    if require_auto_switch and not workflow.resume_on_restart:
        return None

    target_browser_id = int(run.browser_id or workflow.browser_id or 0)
    if not target_browser_id:
        return None
    # 同浏览器并发防护：已有存活运行时不打断（下次启动/手动再恢复）
    running_run_id = await workflow_run_crud_svr.get_running_for_browser(
        run.mid, target_browser_id
    )
    if running_run_id:
        logger.info(
            f"[WorkflowRunner] 目标浏览器已有运行，暂不恢复: "
            f"browser={target_browser_id}, running={running_run_id}, "
            f"interrupted={run.run_id}"
        )
        return None

    # 先消费恢复标记：同一条中断只恢复一次，避免启动扫描与手动点击并发重复拉起
    await workflow_run_crud_svr.mark_resume_consumed(run.run_id)
    new_run_id = launch_workflow_run(
        workflow,
        trigger_source=run.trigger_source,
        browser_id=target_browser_id,
        resume_from=run,
    )
    logger.warning(
        f"[WorkflowRunner] 已从断点恢复运行: workflow={workflow.workflow_id}, "
        f"old_run={run.run_id}, new_run={new_run_id}, "
        f"checkpoint={run.checkpoint_step or run.completed_steps}, "
        f"resume_count={int(run.resume_count or 0) + 1}"
    )
    return new_run_id


async def resume_interrupted_runs() -> list[str]:
    """启动恢复：自动拉起「服务重启导致中断」且工作流开启了自动恢复的运行

    必须在 ``interrupt_stale_running()`` 之后调用（此时残留 RUNNING 已收口为
    INTERRUPTED 并写入可恢复标记）。单条失败只告警，不阻塞服务启动。

    Returns:
        本次成功恢复出的新 run_id 列表。
    """
    if not settings.workflow_resume_on_startup:
        return []

    resumed: list[str] = []
    try:
        candidates = await workflow_run_crud_svr.list_resumable(
            max_age_seconds=settings.workflow_resume_max_age_seconds
        )
    except Exception as exc:  # noqa: BLE001 - 恢复扫描失败不阻塞启动
        logger.warning(f"[WorkflowRunner] 扫描可恢复运行失败: {exc}")
        return resumed

    for run in candidates:
        try:
            new_run_id = await resume_run_record(run, require_auto_switch=True)
            if new_run_id:
                resumed.append(new_run_id)
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响其它恢复
            logger.warning(
                f"[WorkflowRunner] 恢复运行失败: run={run.run_id}, error={exc}"
            )
    if resumed:
        logger.warning(
            f"[WorkflowRunner] 启动恢复完成：{len(resumed)} 条运行已从断点继续: "
            f"{resumed[:20]}"
        )
    else:
        logger.info("[WorkflowRunner] 无可自动恢复的中断运行")
    return resumed
