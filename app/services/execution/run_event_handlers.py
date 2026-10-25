"""工作流运行状态事件的默认订阅者

见 ``docs/工作流运行状态机计划书.md`` §7：状态落库后由事件驱动做「派生态」维护，
执行主体不再直接写工作流的 ``last_run_status / last_run_at``。

新增订阅者时注意：
    - 幂等：同一条终态事件可能重复到达（幂等写入 / 兜底收口）；
    - 只读 DB 或用独立事务，失败只记日志，不能反向影响运行记录；
    - 不要在这里做耗时操作（会占用事件循环，必要时自己 create_task）。
"""

from __future__ import annotations

from loguru import logger

from app.models.database.workflow.models import WorkflowRunTriggerEnum
from app.services.execution.crud_service import (
    workflow_crud_svr,
    workflow_run_crud_svr,
)
from app.services.execution.run_events import (
    WorkflowRunStatusChanged,
    run_event_bus,
)
from app.services.execution.run_state_machine import is_terminal


async def log_run_status_changed(event: WorkflowRunStatusChanged) -> None:
    """审计日志：每次状态迁移留一条结构化记录，便于排障（run_id 可关联日志）"""
    previous = event.from_status.value if event.from_status else "∅"
    reason = f", reason={event.reason}" if event.reason else ""
    if event.from_status is event.to_status:
        # 幂等重复写入：不重复刷日志
        return
    logger.info(
        f"[RunEvent] run={event.run_id} {previous} → {event.to_status.value}{reason}"
    )


async def sync_workflow_last_run(event: WorkflowRunStatusChanged) -> None:
    """终态时刷新工作流的 ``last_run_status / last_run_at``。

    这两个字段只是「卡片展示用的派生快照」，运行记录才是真相；因此这条订阅失败
    （DB 抖动 / 并发更新）不会影响运行本身，也不会回滚状态。
    """
    if not is_terminal(event.to_status):
        return

    record = await workflow_run_crud_svr.get_by_run_id(event.run_id)
    if record is None:
        logger.debug(f"[RunEvent] 终态事件找不到运行记录，跳过快照刷新: run={event.run_id}")
        return

    workflow = await workflow_crud_svr.get_by_workflow_id(record.workflow_id)
    if workflow is None:
        logger.debug(
            f"[RunEvent] 运行记录所属工作流不存在，跳过快照刷新: run={event.run_id}"
        )
        return

    await workflow_crud_svr.update_run_state(
        id=workflow.id,
        status=event.to_status.value,
        last_run_at=record.finished_at or event.occurred_at,
    )


async def settle_run_billing(event: WorkflowRunStatusChanged) -> None:
    """终态时结算时长与使用统计（只有定时任务扣费，见会员权益计划书 §3.1）。

    ``billable_seconds`` 由执行体随摘要带出（拿到会话后才开始计时），这里不再
    自行推算；``settle_workflow_run`` 本身幂等且内部吞异常。
    """
    summary = event.summary
    if summary is None or not is_terminal(event.to_status):
        return

    record = await workflow_run_crud_svr.get_by_run_id(event.run_id)
    if record is None:
        logger.debug(f"[RunEvent] 结算找不到运行记录: run={event.run_id}")
        return

    from app.services.membership.membership_service import MembershipService

    await MembershipService.settle_workflow_run(
        mid=int(record.mid),
        browser_id=int(record.browser_id),
        run_seconds=summary.billable_seconds,
        workflow_id=int(record.workflow_id),
        run_id=record.run_id,
        is_scheduled=record.trigger_source == WorkflowRunTriggerEnum.SCHEDULE,
    )


async def notify_run_finished(event: WorkflowRunStatusChanged) -> None:
    """终态时发送结束通知（成功 / 取消 / 失败），受 ``notify_on_end`` 开关约束。

    通知正文需要的步骤数、耗时、失败原因全部来自事件摘要，无需再读执行期对象。
    """
    summary = event.summary
    if summary is None or not is_terminal(event.to_status):
        return

    record = await workflow_run_crud_svr.get_by_run_id(event.run_id)
    if record is None:
        logger.debug(f"[RunEvent] 通知找不到运行记录: run={event.run_id}")
        return
    if record.notified:
        # 已通知过（重复事件 / 兜底路径）：不重复推送
        return

    workflow = await workflow_crud_svr.get_by_workflow_id(record.workflow_id)
    if workflow is None or not getattr(workflow, "notify_on_end", True):
        return

    if event.to_status.value == "success":
        phase = "success"
    elif event.to_status.value == "cancelled":
        phase = "cancelled"
    else:
        phase = "failure"

    from app.services.execution.workflow.notify import _notify_run

    notified = await _notify_run(
        workflow=workflow,
        mid=int(record.mid),
        browser_id=record.browser_id or None,
        run_id=record.run_id,
        phase=phase,
        summary={
            "total": summary.total,
            "success": summary.success_count,
            "failed": summary.failed_count,
            "tolerated": summary.tolerated_count,
            "tolerated_samples": summary.tolerated_samples,
            "loop_summaries": summary.loop_summaries,
        },
        error_message=summary.error_message,
        duration_ms=summary.duration_ms,
    )
    if notified:
        await workflow_run_crud_svr.mark_notified(record.run_id)


def register_default_run_event_handlers() -> None:
    """注册默认订阅者（服务启动时调用；重复调用幂等）"""
    run_event_bus.subscribe("log_run_status_changed", log_run_status_changed)
    run_event_bus.subscribe("sync_workflow_last_run", sync_workflow_last_run)
    # 以下两个依赖事件摘要（summary），非终态事件会自动跳过
    run_event_bus.subscribe("settle_run_billing", settle_run_billing)
    run_event_bus.subscribe("notify_run_finished", notify_run_finished)
    logger.info(
        f"✅ 运行状态事件订阅已注册: {run_event_bus.handler_names}"
    )


__all__ = [
    "log_run_status_changed",
    "notify_run_finished",
    "register_default_run_event_handlers",
    "settle_run_billing",
    "sync_workflow_last_run",
]
