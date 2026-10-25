"""运行状态事件总线单测（docs/工作流运行状态机计划书.md §7）

只测总线本身：分发、异步订阅、异常隔离、重名覆盖、空 run_id 忽略。
默认订阅者涉及数据库，不在这里覆盖。
"""

import asyncio

from app.models.database.workflow.models import WorkflowRunStatusEnum as S
from app.services.execution.run_events import (
    WorkflowRunEventBus,
    WorkflowRunStatusChanged,
    WorkflowRunSummary,
    publish_status_changed,
    run_event_bus,
)


def test_event_defaults() -> None:
    event = WorkflowRunStatusChanged(run_id="r1", to_status=S.RUNNING)
    assert event.from_status is None
    assert event.reason is None
    assert event.occurred_at is not None
    assert event.summary is None


def test_event_carries_summary_to_handlers() -> None:
    bus = WorkflowRunEventBus()
    received: list[WorkflowRunSummary | None] = []
    bus.subscribe("h", lambda e: received.append(e.summary))

    summary = WorkflowRunSummary(
        total=10,
        success_count=9,
        failed_count=1,
        duration_ms=1234.0,
        billable_seconds=60.4,
        error_message="1 个步骤失败",
    )
    bus.publish(
        WorkflowRunStatusChanged(
            run_id="r1", from_status=S.RUNNING, to_status=S.FAILED, summary=summary
        )
    )

    assert received == [summary]
    assert received[0] is not None
    assert received[0].billable_seconds == 60.4


def test_sync_handler_receives_event_in_order() -> None:
    bus = WorkflowRunEventBus()
    seen: list[str] = []
    bus.subscribe("a", lambda e: seen.append(f"a:{e.to_status.value}"))
    bus.subscribe("b", lambda e: seen.append(f"b:{e.to_status.value}"))

    bus.publish(WorkflowRunStatusChanged(run_id="r1", to_status=S.QUEUED))

    assert seen == ["a:queued", "b:queued"]
    assert bus.handler_names == ["a", "b"]


def test_sync_handler_exception_does_not_block_others() -> None:
    bus = WorkflowRunEventBus()
    seen: list[str] = []

    def boom(_: WorkflowRunStatusChanged) -> None:
        raise RuntimeError("handler 挂了")

    bus.subscribe("boom", boom)
    bus.subscribe("ok", lambda e: seen.append(e.to_status.value))

    bus.publish(WorkflowRunStatusChanged(run_id="r1", to_status=S.RUNNING))

    assert seen == ["running"]


def test_async_handler_runs_on_loop() -> None:
    async def scenario() -> list[str]:
        bus = WorkflowRunEventBus()
        seen: list[str] = []

        async def handler(event: WorkflowRunStatusChanged) -> None:
            await asyncio.sleep(0)
            seen.append(event.to_status.value)

        bus.subscribe("async", handler)
        bus.publish(WorkflowRunStatusChanged(run_id="r1", to_status=S.CANCELLED))
        # 发布是同步的，异步订阅者在事件循环下一轮执行
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return seen

    assert asyncio.run(scenario()) == ["cancelled"]


def test_async_handler_exception_is_swallowed() -> None:
    async def scenario() -> list[str]:
        bus = WorkflowRunEventBus()
        seen: list[str] = []

        async def boom(_: WorkflowRunStatusChanged) -> None:
            raise RuntimeError("async handler 挂了")

        bus.subscribe("boom", boom)
        bus.subscribe("ok", lambda e: seen.append(e.to_status.value))
        bus.publish(WorkflowRunStatusChanged(run_id="r1", to_status=S.SUCCESS))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return seen

    # 异步订阅者抛错不影响同步订阅者，也不冒泡
    assert asyncio.run(scenario()) == ["success"]


def test_subscribe_same_name_replaces_handler() -> None:
    bus = WorkflowRunEventBus()
    seen: list[str] = []
    bus.subscribe("h", lambda e: seen.append("first"))
    bus.subscribe("h", lambda e: seen.append("second"))

    bus.publish(WorkflowRunStatusChanged(run_id="r1", to_status=S.FAILED))

    assert seen == ["second"]
    assert bus.handler_names == ["h"]


def test_publish_without_running_loop_drops_async_handler() -> None:
    bus = WorkflowRunEventBus()
    executed: list[str] = []

    async def handler(_: WorkflowRunStatusChanged) -> None:  # pragma: no cover
        executed.append("x")

    bus.subscribe("async", handler)
    # 同步上下文没有事件循环：应安全丢弃，不抛 RuntimeWarning/异常
    bus.publish(WorkflowRunStatusChanged(run_id="r1", to_status=S.RUNNING))

    assert executed == []


def test_publish_status_changed_ignores_empty_run_id() -> None:
    # 全局总线未注册订阅者时也应安全（默认订阅者由 setup 注册）
    publish_status_changed("", S.RUNNING)
    publish_status_changed("r1", S.RUNNING)
    assert isinstance(run_event_bus, WorkflowRunEventBus)
