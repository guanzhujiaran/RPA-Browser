"""工作流异步启动 / 任务收尾 / 断点恢复

从 ``workflow_runner`` 拆出：

- :func:`launch_workflow_run`：Docker ``docker run`` 语义——立即返回 run_id，
  执行在后台 ``asyncio.Task`` 中进行，支持多浏览器（并发 / 串行）；
- :func:`_on_run_task_done` / :func:`finalize_run_after_task`：task 收尾与统一补收口
  （「停止工作流」复用同一套收尾，不另写清理逻辑）；
- :func:`resume_run_record` / :func:`resume_interrupted_runs`：重启断点恢复；
- :func:`requeue_pending_runs`：重启后把 ``status=QUEUED`` 的运行重新入队
  （见 docs/同浏览器工作流排队计划书.md）。

恢复语义为**至少一次**：从 ``checkpoint_step`` 之后继续，当前步骤重跑；
循环内已处理资源由 WorkflowResourceRecord（success/skipped）跳过。
"""

from __future__ import annotations

import asyncio
from functools import partial

from loguru import logger

from app.config import settings
from app.models.common.exceptions.base_exception import BrowserNotStartedException
from app.models.database.workflow.models import (
    UserWorkflow,
    WorkflowRunRecord,
    WorkflowRunStatusEnum,
    WorkflowRunTriggerEnum,
)
from app.services.RPA_browser.session.live_service import live_service
from app.services.execution.cancellation import cancellation_registry
from app.services.execution.crud_service import (
    workflow_crud_svr,
    workflow_run_crud_svr,
)
from app.services.execution.run_tasks import workflow_run_tasks
from app.services.execution.workflow.run_workflow import run_workflow


async def launch_workflow_run(
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

    # 单浏览器：落 QUEUED 记录（排队凭证 + 立即入队）后交给后台执行
    if len(targets) <= 1:
        target = targets[0] if targets else None
        await workflow_run_crud_svr.create_queued(
            workflow_id=int(workflow.workflow_id),
            mid=int(workflow.mid),
            browser_id=target,
            trigger_source=trigger_source,
            run_id=run_id,
        )
        _spawn(run_id, target)
        logger.info(
            f"[WorkflowRunner] 已受理并进入队列: workflow={workflow.workflow_id}, "
            f"trigger={trigger_source}, browser={target}, run={run_id}"
        )
        return run_id

    # ── 多浏览器：每个浏览器一次独立运行；并发开关决定是否同时跑 ──
    run_ids = [run_id] + [workflow_run_crud_svr.new_run_id() for _ in targets[1:]]
    for rid, bid in zip(run_ids, targets):
        await workflow_run_crud_svr.create_queued(
            workflow_id=int(workflow.workflow_id),
            mid=int(workflow.mid),
            browser_id=bid,
            trigger_source=trigger_source,
            run_id=rid,
        )
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
            # 停止工作流会把后续排队记录直接置 CANCELLED：这里必须跳过，
            # 否则 _open_run 会为「已取消」的记录重新建 RUNNING 记录，停止等于无效
            record = await workflow_run_crud_svr.get_by_run_id(rid)
            if record is not None and record.status == WorkflowRunStatusEnum.CANCELLED:
                logger.info(
                    f"[WorkflowRunner] 跳过已取消的浏览器运行: run={rid}, browser={bid}"
                )
                continue
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
    """运行 task 结束回调：把收尾交给统一的 :func:`finalize_run_after_task`

    执行权必须在这里释放（而不是 ``run_workflow`` 内部的 finally）：后者在写终态
    **之前**触发，此时 DB 里本次运行仍是 RUNNING，下一位排队者的并发检查会把它
    当成「同浏览器已有工作流在执行」而失败。

    ``done_callback`` 内不能 await，故这里只把收尾丢回事件循环。异常路径（task 在
    写终态前被取消）由收尾函数补收口为 INTERRUPTED，避免运行记录永久停留在 RUNNING。

    注意：task 抛出的异常如果不在这里取出来记日志，会被 asyncio 静默吞掉——
    表现为「运行结束但记录还停在 RUNNING，随后被兜底收口成中断」，而终端里
    没有任何异常信息可查。因此这里必须显式 log ``task.exception()``。
    """
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            logger.error(
                f"[WorkflowRunner] 运行任务异常退出: run={run_id}, "
                f"{type(exc).__name__}: {exc}"
            )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 事件循环已关闭（进程关停）：无循环可用，只清句柄，交由启动恢复 (interrupt_stale_running) 兜底
        workflow_run_tasks.clear(run_id)
        return
    loop.create_task(finalize_run_after_task(run_id))


async def finalize_run_after_task(run_id: str) -> bool:
    """一次运行结束后的**统一收尾**（正常结束 / 被停止 / task 早已消失）。

    为什么收敛成一个函数：task 的 done_callback 与「停止工作流」（本实例已无存活
    task 的僵尸记录、同工作流的其它运行）需要对同一条记录做同一套清理，停止路径
    不应另写一份（见 ``docs/工作流停止计划书.md`` §3.4）。

    幂等：正常路径下 ``run_workflow`` 已写终态，除清理句柄外均为空操作。

    做四件事：
    1. 清理运行 task 句柄，避免注册表残留；
    2. 记录仍为 RUNNING（task 在写终态前被取消 / 协程丢失）→ 补收口为 INTERRUPTED；
    3. 清掉会话上残留的执行期标记（仅当仍指向该运行），否则该浏览器无法再启动工作流；
    4. 清掉取消标记，避免键残留。

    Returns:
        True 表示确实补收口了一条仍停留在 RUNNING 的记录。
    """
    workflow_run_tasks.clear(run_id)
    reaped = False
    record: WorkflowRunRecord | None = None
    try:
        record = await workflow_run_crud_svr.get_by_run_id(run_id)
        if record is not None and record.status == WorkflowRunStatusEnum.RUNNING:
            reaped = bool(
                await workflow_run_crud_svr.interrupt_stale_running(
                    reason="运行任务已结束但未正常收口，自动收口为中断",
                    run_ids=[run_id],
                )
            )
            logger.warning(
                f"[WorkflowRunner] 运行任务结束但未收口，已补收口为中断: run={run_id}"
            )
    except Exception as exc:  # noqa: BLE001 - 兜底收口失败不打断事件循环
        logger.warning(f"[WorkflowRunner] 补收口运行失败: run={run_id}, {exc}")

    # 会话执行期标记：run_workflow.finally 正常会解除；task 被强制取消 / 跨进程残留时兜底
    if record is not None:
        try:
            entry = live_service.get_browser_session_entry(
                mid=record.mid, browser_id=record.browser_id
            )
            if entry is not None and run_id in entry.active_workflow_runs:
                live_service.end_workflow_run(record.mid, record.browser_id, run_id)
        except BrowserNotStartedException:
            pass
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响收口
            logger.warning(
                f"[WorkflowRunner] 清理会话执行标记失败: run={run_id}, {exc}"
            )

    cancellation_registry.clear(run_id)
    return reaped


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
    new_run_id = await launch_workflow_run(
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


# ───────────────────────── 重启后重新排队 ─────────────────────────


def _spawn_existing_run(
    workflow: UserWorkflow,
    *,
    run_id: str,
    browser_id: int,
    trigger_source: WorkflowRunTriggerEnum,
) -> asyncio.Task:
    """为「已存在的排队记录」重新拉起执行 task（重启重排专用）。"""
    task = asyncio.create_task(
        run_workflow(
            workflow,
            trigger_source=trigger_source,
            browser_id=browser_id,
            # MCP/HTTP 类动作需要 mid 头；重启后没有原始请求头，用工作流所属 mid 兜底
            auth_headers={"x-bili-mid": str(workflow.mid)},
            run_id=run_id,
        ),
        name=f"workflow-run:{run_id}",
    )
    workflow_run_tasks.register(run_id, task)
    task.add_done_callback(partial(_on_run_task_done, run_id))
    return task


async def requeue_pending_runs() -> list[str]:
    """启动重排：把重启前处于 ``QUEUED`` 的运行按提交顺序重新入队。

    语义边界（见 ``docs/同浏览器工作流排队计划书.md`` §4）：

    - ``QUEUED``：重新排队，保持原顺序（本函数负责）；
    - ``RUNNING``：已由 ``interrupt_stale_running()`` 收口为 ``INTERRUPTED``，
      仅 ``resume_on_restart=True`` 的工作流走断点续跑，**不自动重排**（避免
      无去重记录的工作流重复产生副作用）。

    必须在 ``interrupt_stale_running()`` 之后调用，且不阻塞启动：单条失败只告警。
    """
    try:
        rows = await workflow_run_crud_svr.list_queued()
    except Exception as exc:  # noqa: BLE001 - 扫描失败不阻塞启动
        logger.warning(f"[WorkflowRunner] 扫描排队运行失败: {exc}")
        return []

    if not rows:
        logger.info("[WorkflowRunner] 无待重排的排队运行")
        return []

    requeued: list[str] = []
    for run in rows:
        try:
            workflow = await workflow_crud_svr.get_by_workflow_id(run.workflow_id)
            if workflow is None or not workflow.is_enabled:
                await workflow_run_crud_svr.finish_queued(
                    run.run_id,
                    status=WorkflowRunStatusEnum.FAILED,
                    error_message="工作流不存在或已禁用，排队任务已取消",
                )
                continue

            target_browser_id = int(run.browser_id or workflow.browser_id or 0)
            if not target_browser_id:
                await workflow_run_crud_svr.finish_queued(
                    run.run_id,
                    status=WorkflowRunStatusEnum.FAILED,
                    error_message="未指定执行浏览器，排队任务已取消",
                )
                continue

            trigger_source = (
                run.trigger_source
                if isinstance(run.trigger_source, WorkflowRunTriggerEnum)
                else WorkflowRunTriggerEnum(run.trigger_source)
            )
            _spawn_existing_run(
                workflow,
                run_id=run.run_id,
                browser_id=target_browser_id,
                trigger_source=trigger_source,
            )
            requeued.append(run.run_id)
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响其它
            logger.warning(
                f"[WorkflowRunner] 重排失败: run={run.run_id}, error={exc}"
            )

    if requeued:
        logger.warning(
            f"[WorkflowRunner] 已重排 {len(requeued)} 条排队运行: {requeued[:20]}"
        )
    return requeued
