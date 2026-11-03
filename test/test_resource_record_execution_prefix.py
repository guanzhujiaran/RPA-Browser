"""「已处理资源」列表按 execution_id 查询：必须支持**前缀**匹配。

背景（2026-11-03 线上反馈）：前端「执行批次」列只展示 `run_id` 前 8 位
（完整值是 32 位 hex，`uuid4().hex`），用户按看到的短 id 填进筛选框后，
后端做的是精确等值匹配 → 永远返回空列表。

修复口径：
- 只读列表（`list_records` / `list_history`）按**前缀**匹配，完整 id 本身也是自身前缀；
- 删除等破坏性操作（`delete` / `clear`）保持**精确**匹配，避免一次删掉多个批次。
"""

import pytest
from sqlmodel import select

from app.models.base.base_sqlmodel import BasePaginationReq
from app.models.database.workflow.resource_record import (
    ResourceProcessStatus,
    WorkflowResourceRecord,
)
from app.services.execution.crud_service.resource_record_crud import ResourceRecordService
from app.utils.depends.session_manager import DatabaseSessionManager

MID = "7788990011"
WORKFLOW_ID = 990011223344
BROWSER_ID = 1
RESOURCE_TYPE = "lottery"

# 两个同前缀的批次 + 一个不同批次
PREFIX = "51524b39"
EXEC_A = "51524b39" + "a" * 24
EXEC_B = "51524b39" + "b" * 24
EXEC_OTHER = "ffffffff" + "c" * 24


async def _insert(resource_id: str, execution_id: str) -> None:
    async with DatabaseSessionManager.async_session() as session:
        session.add(
            WorkflowResourceRecord(
                mid=MID,
                workflow_id=WORKFLOW_ID,
                browser_id=BROWSER_ID,
                execution_id=execution_id,
                resource_type=RESOURCE_TYPE,
                resource_id=resource_id,
                status=ResourceProcessStatus.SUCCESS,
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
    """写入三行样例数据，测试结束后清理。"""
    await _cleanup()
    await _insert("1001", EXEC_A)
    await _insert("1002", EXEC_B)
    await _insert("1003", EXEC_OTHER)
    try:
        yield
    finally:
        await _cleanup()


async def _list(execution_id: str | None):
    return await ResourceRecordService.list_records(
        MID,
        WORKFLOW_ID,
        BasePaginationReq(page=1, per_page=50),
        resource_type=RESOURCE_TYPE,
        execution_id=execution_id,
    )


async def test_short_prefix_matches_all_batches(sample_rows) -> None:
    """填页面展示的 8 位短 id → 命中同前缀的两条记录。"""
    resp = await _list(PREFIX)

    assert resp.total == 2
    assert {row.resource_id for row in resp.items} == {"1001", "1002"}


async def test_full_execution_id_still_exact(sample_rows) -> None:
    """完整 id 仍等价精确匹配（前缀匹配的自然结果）。"""
    resp = await _list(EXEC_A)

    assert resp.total == 1
    assert resp.items[0].resource_id == "1001"


async def test_unmatched_prefix_returns_empty(sample_rows) -> None:
    """不存在的批次前缀 → 空结果。"""
    resp = await _list("deadbeef")

    assert resp.total == 0
    assert resp.items == []


async def test_blank_execution_id_does_not_filter(sample_rows) -> None:
    """空白值 = 不过滤（避免 `LIKE '%'` 意外命中，也避免把首尾空格当条件）。"""
    resp = await _list("   ")

    assert resp.total == 3
