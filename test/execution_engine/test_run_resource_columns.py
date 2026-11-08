"""运行记录的资源级结果列与 fail_reason 分类（P2）

见 docs/通知动作与运行告警计划书.md §D5.1：
- 终态 `finish()` 把资源四元组与软 / 致命失败分类写入 `workflowrunrecord`；
- `to_dict()` 对外暴露同一口径，避免「18/18 步骤成功」却 `status=FAILED` 无法自解释。
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.models.database.workflow.models import (
    WorkflowRunRecord,
    WorkflowRunStatusEnum,
)
from app.services.execution.crud_service.workflow_run_crud import (
    workflow_run_crud_svr,
)
from app.models.workflow.workflow_responses import WorkflowRunLogItemResponse
from app.services.execution.run_events import WorkflowRunSummary
from app.utils.depends.session_manager import DatabaseSessionManager

RUN_ID = "run-resource-columns-test"
WORKFLOW_ID = 998877110022
MID = "5566778899"


async def _cleanup() -> None:
    async with DatabaseSessionManager.async_session() as session:
        rows = (
            await session.exec(  # type: ignore[attr-defined]
                select(WorkflowRunRecord).where(WorkflowRunRecord.run_id == RUN_ID)
            )
        ).all()
        for row in rows:
            await session.delete(row)
        await session.commit()


async def _insert_running() -> None:
    async with DatabaseSessionManager.async_session() as session:
        session.add(
            WorkflowRunRecord(
                run_id=RUN_ID,
                workflow_id=WORKFLOW_ID,
                mid=MID,
                browser_id=1,
                status=WorkflowRunStatusEnum.RUNNING,
                total=18,
            )
        )
        await session.commit()


async def _read() -> WorkflowRunRecord:
    async with DatabaseSessionManager.async_session() as session:
        row = (
            await session.exec(  # type: ignore[attr-defined]
                select(WorkflowRunRecord).where(WorkflowRunRecord.run_id == RUN_ID)
            )
        ).first()
    assert row is not None
    return row


@pytest.fixture(autouse=True)
async def _clean():
    await _cleanup()
    yield
    await _cleanup()


def _summary(*, soft: int, fatal: int) -> WorkflowRunSummary:
    """2026-11-08 run de801661 的口径：153 条资源，18 条失败。"""
    return WorkflowRunSummary(
        total=18,
        success_count=18,
        resource_total=153,
        resource_success=134,
        resource_failed=soft + fatal,
        resource_skipped=1,
        resource_soft_failed=soft,
        resource_fatal_failed=fatal,
    )


async def test_finish_persists_resource_stats() -> None:
    """全部是可重试软失败时结论成功，但记录里仍能看到 18 条失败及其分类。"""
    await _insert_running()

    ok = await workflow_run_crud_svr.finish(
        run_id=RUN_ID,
        status=WorkflowRunStatusEnum.SUCCESS,
        success_count=18,
        failed_count=0,
        duration_ms=1000.0,
        summary=_summary(soft=18, fatal=0),
    )

    assert ok
    row = await _read()
    assert row.status == WorkflowRunStatusEnum.SUCCESS
    assert (
        row.resource_total,
        row.resource_success,
        row.resource_failed,
        row.resource_skipped,
    ) == (153, 134, 18, 1)
    assert row.resource_soft_failed == 18
    assert row.resource_fatal_failed == 0


async def test_finish_persists_fatal_failures() -> None:
    """致命失败触发降级时，记录里可区分致命与可重试两部分。"""
    await _insert_running()

    ok = await workflow_run_crud_svr.finish(
        run_id=RUN_ID,
        status=WorkflowRunStatusEnum.FAILED,
        success_count=18,
        failed_count=0,
        error_message="资源失败达到阈值",
        summary=_summary(soft=17, fatal=1),
    )

    assert ok
    row = await _read()
    assert row.status == WorkflowRunStatusEnum.FAILED
    assert row.resource_soft_failed == 17
    assert row.resource_fatal_failed == 1


async def test_finish_without_summary_writes_zeros() -> None:
    """前置校验失败等无 summary 的路径写 0，不能留下脏值。"""
    await _insert_running()

    ok = await workflow_run_crud_svr.finish(
        run_id=RUN_ID,
        status=WorkflowRunStatusEnum.FAILED,
        error_message="前置校验失败",
    )

    assert ok
    row = await _read()
    assert row.resource_total == 0
    assert row.resource_failed == 0
    assert row.resource_fatal_failed == 0


async def test_to_dict_exposes_resource_stats() -> None:
    record = WorkflowRunRecord(
        run_id=RUN_ID,
        workflow_id=WORKFLOW_ID,
        mid=MID,
        browser_id=1,
        resource_total=153,
        resource_success=134,
        resource_failed=18,
        resource_skipped=1,
        resource_soft_failed=18,
        resource_fatal_failed=0,
    )

    data = await workflow_run_crud_svr.to_dict(record)

    assert data["resource_total"] == 153
    assert data["resource_success"] == 134
    assert data["resource_failed"] == 18
    assert data["resource_skipped"] == 1
    assert data["resource_soft_failed"] == 18
    assert data["resource_fatal_failed"] == 0


async def test_response_model_exposes_resource_stats() -> None:
    """to_dict 下发的资源字段必须能被响应模型接收，否则会被 Pydantic 静默丢弃。"""
    record = WorkflowRunRecord(
        run_id=RUN_ID,
        workflow_id=WORKFLOW_ID,
        mid=MID,
        browser_id=1,
        resource_total=153,
        resource_success=134,
        resource_failed=18,
        resource_skipped=1,
        resource_soft_failed=18,
        resource_fatal_failed=0,
    )

    data = await workflow_run_crud_svr.to_dict(record)
    # 未持久化的记录没有主键，而响应模型的 id 是必填 int
    data["id"] = 1
    resp = WorkflowRunLogItemResponse(**data, queue_position=0)

    assert (
        resp.resource_total,
        resp.resource_success,
        resp.resource_failed,
        resp.resource_skipped,
        resp.resource_soft_failed,
        resp.resource_fatal_failed,
    ) == (153, 134, 18, 1, 18, 0)
