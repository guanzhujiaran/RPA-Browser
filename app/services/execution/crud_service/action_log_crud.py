"""
浏览器操作日志 CRUD 服务

仅保留日志记录的写入与查询：
    ActionLogCrudService — 日志记录的写入、查询、删除、清空、统计、过期清理

（采集「是否启用 / 采集哪些字段」已直接落到 action 的基础配置上，
 自定义操作见 CompositeActionModel 的 log_* 字段，内置操作见 settings.action_log_*，
 不再有独立的采集配置表。）
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import delete as sa_delete
from sqlmodel import func, select

from app.config import settings
from app.models.database.log.models import (
    ActionLogRecord,
    ActionLogSourceEnum,
    ActionLogStatusEnum,
)
from app.utils.depends.session_manager import DatabaseSessionManager


class ActionLogCrudService:
    """操作日志记录 CRUD"""

    @staticmethod
    async def create(record: ActionLogRecord) -> ActionLogRecord:
        async with DatabaseSessionManager.async_session() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    @staticmethod
    def _build_filters(
        mid: int | str,
        *,
        action_id: str | None = None,
        execution_id: str | None = None,
        workflow_id: str | None = None,
        browser_id: str | None = None,
        source: ActionLogSourceEnum | None = None,
        status: ActionLogStatusEnum | None = None,
        success: bool | None = None,
        keyword: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> list:
        conditions: list = [ActionLogRecord.mid == str(mid)]
        if action_id:
            conditions.append(ActionLogRecord.action_id == action_id)
        if execution_id:
            conditions.append(ActionLogRecord.execution_id == execution_id)
        if workflow_id:
            conditions.append(ActionLogRecord.workflow_id == workflow_id)
        if browser_id:
            conditions.append(ActionLogRecord.browser_id == str(browser_id))
        if source:
            conditions.append(ActionLogRecord.source == source)
        if status:
            conditions.append(ActionLogRecord.status == status)
        if success is not None:
            conditions.append(ActionLogRecord.success == success)
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                ActionLogRecord.action_name.like(like)  # type: ignore[attr-defined]
                | ActionLogRecord.action_id.like(like)  # type: ignore[attr-defined]
                | ActionLogRecord.error_message.like(like)  # type: ignore[attr-defined]
            )
        if started_after:
            conditions.append(ActionLogRecord.started_at >= started_after)
        if started_before:
            conditions.append(ActionLogRecord.started_at <= started_before)
        return conditions

    @classmethod
    async def count(cls, mid: int | str, **filters) -> int:
        conditions = cls._build_filters(mid, **filters)
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(func.count(ActionLogRecord.id)).where(*conditions)
            )
            return result.one()

    @classmethod
    async def list(
        cls,
        mid: int | str,
        *,
        skip: int = 0,
        limit: int = 20,
        order_desc: bool = True,
        **filters,
    ) -> List[ActionLogRecord]:
        conditions = cls._build_filters(mid, **filters)
        order_col = (
            ActionLogRecord.id.desc() if order_desc  # type: ignore[attr-defined]
            else ActionLogRecord.id.asc()  # type: ignore[attr-defined]
        )
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ActionLogRecord)
                .where(*conditions)
                .order_by(order_col)
                .offset(skip)
                .limit(limit)
            )
            return list(result.all())

    @staticmethod
    async def get_by_log_id(mid: int | str, log_id: str) -> ActionLogRecord | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ActionLogRecord).where(
                    ActionLogRecord.mid == str(mid),
                    ActionLogRecord.log_id == log_id,
                )
            )
            return result.first()

    @staticmethod
    async def list_by_execution(
        mid: int | str, execution_id: str
    ) -> List[ActionLogRecord]:
        """按执行批次拉取完整链路（按写入顺序升序）"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ActionLogRecord)
                .where(
                    ActionLogRecord.mid == str(mid),
                    ActionLogRecord.execution_id == execution_id,
                )
                .order_by(ActionLogRecord.id.asc())  # type: ignore[attr-defined]
            )
            return list(result.all())

    @staticmethod
    async def delete_by_log_ids(mid: int | str, log_ids: List[str]) -> int:
        if not log_ids:
            return 0
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                sa_delete(ActionLogRecord).where(
                    ActionLogRecord.mid == str(mid),
                    ActionLogRecord.log_id.in_(log_ids),  # type: ignore[attr-defined]
                )
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def clear(cls, mid: int | str, **filters) -> int:
        """按条件批量清理日志"""
        conditions = cls._build_filters(mid, **filters)
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                sa_delete(ActionLogRecord).where(*conditions)
            )
            await session.commit()
            return result.rowcount or 0

    @staticmethod
    async def stats(mid: int | str, days: int = 7) -> Dict[str, Any]:
        """按 action 聚合统计最近 N 天的执行情况"""
        since = datetime.now() - timedelta(days=max(days, 1))
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(
                    ActionLogRecord.action_id,
                    ActionLogRecord.action_name,
                    func.count(ActionLogRecord.id),
                    func.sum(ActionLogRecord.success),
                    func.avg(ActionLogRecord.execution_time),
                )
                .where(
                    ActionLogRecord.mid == str(mid),
                    ActionLogRecord.started_at >= since,
                )
                .group_by(ActionLogRecord.action_id, ActionLogRecord.action_name)
                .order_by(func.count(ActionLogRecord.id).desc())
            )
            rows = result.all()

        items = []
        total, total_success = 0, 0
        for action_id, action_name, cnt, success_cnt, avg_time in rows:
            cnt = int(cnt or 0)
            success_cnt = int(success_cnt or 0)
            total += cnt
            total_success += success_cnt
            items.append({
                "action_id": action_id,
                "action_name": action_name or "",
                "total": cnt,
                "success": success_cnt,
                "failed": cnt - success_cnt,
                "avg_execution_time": round(float(avg_time or 0.0), 4),
            })

        return {
            "days": days,
            "total": total,
            "success": total_success,
            "failed": total - total_success,
            "items": items,
        }

    @staticmethod
    async def cleanup_expired() -> int:
        """按服务端默认保留天数清理过期日志，返回清理条数"""
        retention = settings.action_log_default_retention_days
        if not retention or retention <= 0:
            return 0
        cutoff = datetime.now() - timedelta(days=int(retention))
        deleted = 0
        async with DatabaseSessionManager.async_session() as session:
            res = await session.exec(
                sa_delete(ActionLogRecord).where(ActionLogRecord.started_at < cutoff)
            )
            await session.commit()
            deleted = res.rowcount or 0
        return deleted


action_log_crud_svr = ActionLogCrudService()
