"""运行级资源统计：`ResourceRecordService.count_by_execution`

工作流运行结论的主判据是资源处理结果（success/failed/skipped），
这里锁定口径：
- 只统计本次运行（execution_id）真正处理过的资源；
- failed 计入失败，skipped（404/活动结束）**不算失败**；
- 其它批次的记录不能混进来。
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.models.database.workflow.resource_record import (
    ResourceProcessStatus,
    WorkflowResourceRecord,
)
from app.services.execution.crud_service.resource_record_crud import (
    ResourceRecordService,
)
from app.utils.depends.session_manager import DatabaseSessionManager

MID = "5566778899"
WORKFLOW_ID = 998877665544
BROWSER_ID = 1
RESOURCE_TYPE = "lottery_run_stats"
EXEC_A = "aaaabbbb" + "1" * 24
EXEC_OTHER = "ccccdddd" + "2" * 24


async def _insert(resource_id: str, status: ResourceProcessStatus, exec_id: str) -> None:
    async with DatabaseSessionManager.async_session() as session:
        session.add(
            WorkflowResourceRecord(
                mid=MID,
                workflow_id=WORKFLOW_ID,
                browser_id=BROWSER_ID,
                execution_id=exec_id,
                resource_type=RESOURCE_TYPE,
                resource_id=resource_id,
                status=status,
            )
        )
        await session.commit()


async def _cleanup() -> None:
    async with DatabaseSessionManager.async_session() as session:
        for row in (
            await session.exec(  # type: ignore[attr-defined]
                select(WorkflowResourceRecord).where(
                    WorkflowResourceRecord.mid == MID,
                    WorkflowResourceRecord.workflow_id == WORKFLOW_ID,
                )
            )
        ).all():
            await session.delete(row)
        await session.commit()


@pytest.fixture()
async def sample_rows():
    await _cleanup()
    await _insert("1", ResourceProcessStatus.SUCCESS, EXEC_A)
    await _insert("2", ResourceProcessStatus.SUCCESS, EXEC_A)
    await _insert("3", ResourceProcessStatus.FAILED, EXEC_A)
    await _insert("4", ResourceProcessStatus.SKIPPED, EXEC_A)
    # 其它批次的失败不能污染本次统计
    await _insert("5", ResourceProcessStatus.FAILED, EXEC_OTHER)
    try:
        yield
    finally:
        await _cleanup()


async def test_count_by_execution_groups_by_status(sample_rows) -> None:
    stats = await ResourceRecordService.count_by_execution(EXEC_A)

    assert stats.total == 4
    assert stats.success == 2
    assert stats.failed == 1
    assert stats.skipped == 1
    assert stats.has_data
    assert stats.failed_ratio == 0.25


async def test_skipped_is_not_failure(sample_rows) -> None:
    stats = await ResourceRecordService.count_by_execution(EXEC_A)

    assert stats.failed == 1, "skipped（404/活动结束）不能计入失败"


async def test_unknown_execution_returns_empty() -> None:
    stats = await ResourceRecordService.count_by_execution("no-such-run")

    assert stats.total == 0
    assert stats.failed == 0
    assert not stats.has_data
