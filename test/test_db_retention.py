"""表容量固定上限（``trim_table_to_row_cap``）单测。

只用 SQLite 测试库（见 ``test/conftest.py``），覆盖：
- 保留最新 ``max_rows`` 行，按时间列从旧到新删除；
- 分批删除能完整跑完（批大小 < 需要删除的行数）；
- ``max_rows <= 0`` 时不限制、一行不动。
"""

from datetime import datetime, timedelta

from sqlalchemy import delete as sa_delete
from sqlmodel import select

from app.models.database.log.models import ActionLogRecord
from app.utils.db_retention import trim_table_to_row_cap
from app.utils.depends.session_manager import DatabaseSessionManager

MID = "9900887766"


async def _seed(count: int) -> list[str]:
    """插入 count 条日志（started_at 递增），返回按时间升序的 log_id。"""
    base = datetime(2026, 1, 1, 0, 0, 0)
    log_ids = [f"cap-test-{i:03d}" for i in range(count)]
    async with DatabaseSessionManager.async_session() as session:
        for index, log_id in enumerate(log_ids):
            session.add(
                ActionLogRecord(
                    log_id=log_id,
                    mid=MID,
                    action_id="cap_test_action",
                    started_at=base + timedelta(minutes=index),
                )
            )
        await session.commit()
    return log_ids


async def _cleanup() -> None:
    async with DatabaseSessionManager.async_session() as session:
        await session.execute(
            sa_delete(ActionLogRecord).where(ActionLogRecord.mid == MID)
        )
        await session.commit()


async def _remaining_ids() -> list[str]:
    async with DatabaseSessionManager.async_session() as session:
        rows = (
            await session.exec(  # type: ignore[attr-defined]
                select(ActionLogRecord.log_id)
                .where(ActionLogRecord.mid == MID)
                .order_by(ActionLogRecord.started_at)  # type: ignore[arg-type]
            )
        ).all()
    return list(rows)


async def test_keeps_newest_rows_and_deletes_oldest():
    await _cleanup()
    log_ids = await _seed(5)

    deleted = await trim_table_to_row_cap(
        model=ActionLogRecord,
        order_column="started_at",
        max_rows=3,
        batch_size=100,
        label="Test",
    )

    assert deleted == 2
    assert await _remaining_ids() == log_ids[-3:]


async def test_batch_delete_covers_all_excess_rows():
    await _cleanup()
    log_ids = await _seed(7)

    deleted = await trim_table_to_row_cap(
        model=ActionLogRecord,
        order_column="started_at",
        max_rows=3,
        batch_size=2,
        label="Test",
    )

    assert deleted == 4
    assert await _remaining_ids() == log_ids[-3:]


async def test_zero_max_rows_means_unlimited():
    await _cleanup()
    log_ids = await _seed(4)

    deleted = await trim_table_to_row_cap(
        model=ActionLogRecord,
        order_column="started_at",
        max_rows=0,
        batch_size=2,
        label="Test",
    )

    assert deleted == 0
    assert await _remaining_ids() == log_ids


async def test_no_delete_when_under_cap():
    await _cleanup()
    log_ids = await _seed(3)

    deleted = await trim_table_to_row_cap(
        model=ActionLogRecord,
        order_column="started_at",
        max_rows=10,
        batch_size=2,
        label="Test",
    )

    assert deleted == 0
    assert await _remaining_ids() == log_ids
