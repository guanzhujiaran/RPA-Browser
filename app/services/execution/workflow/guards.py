"""工作流运行守护（僵尸回收 / 会话释放 / 停止宽限期）

从 ``workflow_runner`` 拆出。原文件中三处重复的「查 RUNNING 运行记录」逻辑
统一收敛为 :func:`_list_running_for_browser`。

僵尸判定原则：存活真值取自**本实例 task 注册表**（``workflow_run_tasks``），
不以会话标记 ``entry.workflow_run_id`` 作为存活依据——它恰是崩溃残留的污染源。
"""

from __future__ import annotations

import time
from datetime import timedelta

from loguru import logger
from sqlmodel import select

from app.config import settings
from app.models.database.workflow.models import (
    WorkflowRunRecord,
    WorkflowRunStatusEnum,
)
from app.services.execution.cancellation import cancellation_registry
from app.services.execution.crud_service import workflow_run_crud_svr
from app.services.execution.run_tasks import workflow_run_tasks
from app.utils.time_util import now_local
from app.models.common.exceptions.base_exception import BrowserNotStartedException
from app.services.RPA_browser.session.live_service import live_service
from app.utils.depends.session_manager import DatabaseSessionManager


async def _list_running_for_browser(
    mid: int | str, browser_id: int | str
) -> list[str]:
    """查询某浏览器下所有 RUNNING 运行的 run_id（跨进程兜底的占用判定）"""
    async with DatabaseSessionManager.async_session() as session:
        running_ids = (
            await session.exec(
                select(WorkflowRunRecord.run_id).where(
                    WorkflowRunRecord.mid == str(mid),
                    WorkflowRunRecord.browser_id == int(browser_id),
                    WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING,
                )
            )
        ).all()
    return list(running_ids)


async def _release_browser_after_run(
    mid: int | str,
    browser_id: int | str | None,
    *,
    exclude_run_id: str | None = None,
) -> None:
    """工作流跑完后回收浏览器会话（省资源）。

    只在「该浏览器没有其它工作流仍在运行」且「会话未被用户占用」时释放，
    避免误关正在被其它工作流 / 调试 / 观看者使用的浏览器。

    判定「是否被其它工作流使用」以 DB 的 RUNNING 记录为准（跨进程兜底），
    再用本实例 task 注册表剔除僵尸记录，避免僵尸 RUNNING 永久阻止回收。

    Args:
        exclude_run_id: **必须传本次运行的 run_id**。本函数在 ``finish()`` 之前
            调用（在 ``finally`` 里做资源清理），此时本次运行的记录仍是 RUNNING、
            对应 task 也必然存活，若不排除自身就会被自己挡住 → 会话永不回收
            （表现为「运行已结束/失败，但浏览器一直开着」）。
    """
    if browser_id is None:
        return
    if not settings.workflow_release_browser_after_run:
        return

    # 1) 该浏览器是否还有**其它**存活的工作流运行
    try:
        running_ids = await _list_running_for_browser(mid, browser_id)
    except Exception as exc:  # noqa: BLE001 - 回收失败不影响主流程
        logger.warning(f"[WorkflowRunner] 检查浏览器占用失败，跳过回收: {exc}")
        return

    # 先排除本次运行自身，再剔除本实例已无存活 task 的僵尸记录
    still_running = [
        rid
        for rid in running_ids
        if rid != exclude_run_id and workflow_run_tasks.is_alive(rid)
    ]
    if still_running:
        logger.info(
            f"[WorkflowRunner] 浏览器 {browser_id} 仍有 {len(still_running)} 个工作流在跑，"
            "不回收会话"
        )
        return

    # 2) 会话是否被用户占用（调试 pin / 观看者）
    try:
        entry = live_service.get_browser_session_entry(mid=mid, browser_id=browser_id)
    except BrowserNotStartedException:
        return
    if entry is None:
        return
    if entry.is_pinned:
        logger.info(
            f"[WorkflowRunner] 浏览器 {browser_id} 被自动化任务 pin，不回收会话"
        )
        return
    manager = getattr(entry.browser_session, "webrtc_manager", None)
    if manager is not None and manager.has_live_viewer():
        logger.info(f"[WorkflowRunner] 浏览器 {browser_id} 有观看者，不回收会话")
        return

    # 3) 释放会话
    try:
        await live_service.release_browser_session(int(mid), int(browser_id))
        logger.info(
            f"[WorkflowRunner] 工作流结束，已回收浏览器会话: browser={browser_id}"
        )
    except Exception as exc:  # noqa: BLE001 - 回收失败不影响主流程
        logger.warning(f"[WorkflowRunner] 回收浏览器会话失败: browser={browser_id}, {exc}")


def _release_session_mark(
    run_id: str, mid: int | str, browser_id: int | str | None
) -> None:
    """清除浏览器会话上残留的、指向该已死运行的 ``workflow_run_id`` 标记

    会话标记（``entry.workflow_run_id``）是执行期互斥的载体，正常由
    ``end_workflow_run`` 解除；进程崩溃 / 协程被取消时会残留，使已死运行在会话层面
    仍显示「执行中」，从而继续拦截新运行。此处仅在标记**确实指向该已死运行**时清除，
    不误伤其它活运行。
    """
    if not run_id:
        return
    try:
        entry = live_service.get_browser_session_entry(mid=mid, browser_id=browser_id)
    except BrowserNotStartedException:
        return
    if entry is not None and run_id in entry.active_workflow_runs:
        live_service.end_workflow_run(mid, browser_id, run_id)


async def _reap_dead_run(
    run_id: str, *, mid: int | str, browser_id: int | str | None, reason: str
) -> bool:
    """收口一条「本实例已无存活执行」的僵尸 RUNNING 运行，并清理残留会话标记

    借鉴 Docker：容器被判定已死后，由守护进程统一回收其句柄与占位。此处既把运行
    记录收口为 INTERRUPTED，也清掉会话上的残留标记——两道防护都释放，新运行才能启动。
    """
    reaped = await workflow_run_crud_svr.interrupt_stale_running(
        reason=reason, run_ids=[run_id]
    )
    _release_session_mark(run_id, mid, browser_id)
    return bool(reaped)


async def _collect_zombie_runs(grace_seconds: int) -> list[WorkflowRunRecord]:
    """收集「本实例已无存活执行」的僵尸 RUNNING 运行记录

    判定条件：
     1. 记录仍为 RUNNING 且 ``started_at`` 已超过宽限期——兜底余量，覆盖多实例
        / 未走 task 注册表的执行路径；
     2. **存活真值**：本实例的运行 task 注册表中不存在该 run_id 的存活 task
        （``workflow_run_tasks.is_alive``）。

    注意：**不以会话标记 ``entry.workflow_run_id`` 作为存活依据**——它恰是崩溃残留
    的污染源；把它当作存活信号会导致僵尸永远无法回收，浏览器被永久拦截。

    与启动恢复 (``interrupt_stale_running``) 的区别：后者在启动时收口**全部** RUNNING
    （此时进程内必然没有任何执行），这里只收口本实例已无对应执行的僵尸记录。
    """
    threshold = now_local() - timedelta(seconds=grace_seconds)
    async with DatabaseSessionManager.async_session() as session:
        rows = (
            await session.exec(
                select(WorkflowRunRecord).where(
                    WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING,
                    WorkflowRunRecord.started_at < threshold,
                )
            )
        ).all()

    return [run for run in rows if not workflow_run_tasks.is_alive(run.run_id)]


async def reap_zombie_runs(grace_seconds: int | None = None) -> int:
    """周期任务：自动收口本实例已无存活执行的僵尸 RUNNING 运行

    解决「工作流已挂（进程重启 / 执行会话丢失 / 协程被取消）但运行记录一直显示
    RUNNING」的问题——否则同浏览器并发防护会持续拦截该浏览器的新运行。

    覆盖定时与手动两种触发：定时任务的会话丢失兜底由计费心跳处理，本任务作为统一
    兜底（幂等，行锁 + 状态复检，重复执行不会误伤）。

    Returns:
        本次实际收口的运行条数。
    """
    grace = (
        settings.workflow_stale_run_grace_seconds
        if grace_seconds is None
        else grace_seconds
    )
    try:
        zombie_rows = await _collect_zombie_runs(grace)
        if not zombie_rows:
            return 0
        reaped = await workflow_run_crud_svr.interrupt_stale_running(
            reason="运行实例已丢失（进程重启或执行会话中断），自动收口为中断",
            run_ids=[r.run_id for r in zombie_rows],
        )
        # 同步清掉会话上的残留标记，否则会话级互斥仍会拦截新运行
        for run in zombie_rows:
            _release_session_mark(run.run_id, run.mid, run.browser_id)
        logger.warning(
            f"[WorkflowRunner] 自动收口 {len(reaped)} 条僵尸 RUNNING 运行: "
            f"{[r.run_id for r in reaped][:20]}"
        )
        return len(reaped)
    except Exception as exc:  # noqa: BLE001 - 兜底回收失败不打断调度
        logger.warning(f"[WorkflowRunner] 收口僵尸运行失败: {exc}")
        return 0


async def enforce_stop_grace(grace_seconds: int | None = None) -> int:
    """周期任务：两阶段停止的第二阶段——宽限期超时后强制终止运行 task

    借鉴 Docker ``stop``：先发 SIGTERM（请求协作式停止），宽限期内让执行器在步骤
    边界自然收口；超过宽限期仍存活则 SIGKILL（对 task ``cancel()`` 强制终止）。
    被取消的 task 由 ``run_workflow`` 的 ``except BaseException`` 安全收尾，最终写
    CANCELLED。仅作用于**本实例**确已请求停止且仍存活的运行 task（其余运行不碰）。

    Returns:
        本次强制终止的运行条数。
    """
    grace = (
        settings.workflow_stop_grace_seconds
        if grace_seconds is None
        else grace_seconds
    )
    forced = 0
    try:
        now = time.monotonic()
        for run_id in workflow_run_tasks.live_run_ids():
            requested_at = cancellation_registry.cancel_requested_at(run_id)
            if requested_at is None:
                continue  # 未请求停止：交由僵尸收口 / 自然结束，绝不误杀
            if now - requested_at < grace:
                continue  # 仍在宽限期内，等待协作式收口
            if workflow_run_tasks.cancel(run_id):
                forced += 1
                logger.warning(
                    f"[WorkflowRunner] 停止宽限期超时，强制终止运行 task: run={run_id}"
                )
    except Exception as exc:  # noqa: BLE001 - 强制终止失败不打断调度
        logger.warning(f"[WorkflowRunner] 强制终止停止运行失败: {exc}")
    return forced


async def list_alive_running_for_browser(
    mid: int | str, browser_id: int | str
) -> list[str]:
    """查询某浏览器下**仍存活**（本实例 task 注册表判定）的 RUNNING run_id。

    供运行前置的跨进程并发防护使用：纯 DB 判定会把本实例已崩溃的僵尸记录
    误判为「被占用」，需再叠加注册表存活过滤。
    """
    running_ids = await _list_running_for_browser(mid, browser_id)
    return [rid for rid in running_ids if workflow_run_tasks.is_alive(rid)]
