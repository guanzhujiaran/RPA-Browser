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

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import load_only
from sqlmodel import func, select

from app.config import settings
from app.models.database.log.models import (
    ActionLogRecord,
    ActionLogSourceEnum,
    ActionLogStatusEnum,
)
from app.utils.depends.session_manager import DatabaseSessionManager


# 列表 / 链路查询只加载的轻量字段：排除 params/result_data/variables/logs 等大 JSON 列。
# 这些列单行可达数百 KB，若随列表分页一次性读取并下发，滚动加载几页就能撑爆响应体；
# 完整字段只在「按 log_id 取单条详情」时回表读取。
_SUMMARY_FIELDS = (
    "id",
    "log_id",
    "mid",
    "execution_id",
    "parent_execution_id",
    "depth",
    "action_id",
    "action_name",
    "action_type",
    "source",
    "workflow_id",
    "browser_id",
    "session_id",
    "page_url",
    "status",
    "success",
    "error_message",
    "execution_time",
    "started_at",
    "finished_at",
)

# 转成 ORM 映射属性（load_only 需要映射属性而非字符串）
_SUMMARY_LOAD = tuple(getattr(ActionLogRecord, name) for name in _SUMMARY_FIELDS)


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
    async def create_idempotent(record: ActionLogRecord) -> ActionLogRecord | None:
        """幂等落库：`log_id` 已存在则跳过，返回 None；否则插入并返回记录。

        供 MQ 消费端使用：日志队列是 at-least-once 投递（崩溃重投 / 重复投递），
        以 `log_id` 唯一键做幂等，避免重复记录。
        """
        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(ActionLogRecord.id).where(
                    ActionLogRecord.log_id == record.log_id
                )
            )
            if existing.first() is not None:
                return None
            # 清掉可能残留的主键（重试时 record 可能已带 id），交由自增分配
            record.id = None
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
            conditions.append(ActionLogRecord.action_id == str(action_id))
        if execution_id:
            conditions.append(ActionLogRecord.execution_id == execution_id)
        if workflow_id:
            conditions.append(ActionLogRecord.workflow_id == str(workflow_id))
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
            ActionLogRecord.id.desc()
            if order_desc  # type: ignore[attr-defined]
            else ActionLogRecord.id.asc()  # type: ignore[attr-defined]
        )
        async with DatabaseSessionManager.async_session() as session:
            # 两步查询（主键优先，回表取行），且外层不带 ORDER BY：
            # params/result_data/variables/logs 是 JSON 大列（单行可达 ~1MB，本库
            # 实测 result_data 最大 830KB），而本项目 MySQL 的 sort_buffer_size
            # 被刻意压到 128K。只要这些 JSON 列进入 filesort 的排序元组（即
            # `... ORDER BY id DESC` 的同时 SELECT 了 JSON 列），单行就超过缓冲，
            # 必然抛 1038 Out of sort memory。
            # 因此先在只含 id 的子查询里完成过滤 + 分页 + 排序（排序对象只有整数，
            # 任何执行计划都不会溢出缓冲），再由外层按主键回表；回表不带 ORDER BY，
            # 最终顺序在 Python 侧按 id 还原。
            # 回表时用 load_only 只取轻量字段：大 JSON 列既不必读、也不必下发。
            page_ids = (
                select(ActionLogRecord.id)
                .where(*conditions)
                .order_by(order_col)
                .offset(skip)
                .limit(limit)
                .subquery()
            )
            result = await session.exec(
                select(ActionLogRecord)
                .options(load_only(*_SUMMARY_LOAD))
                .join(page_ids, ActionLogRecord.id == page_ids.c.id)
            )
            rows = list(result.all())
        rows.sort(key=lambda r: r.id or 0, reverse=order_desc)
        return rows

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
        """按执行批次拉取完整链路（按写入顺序升序）

        只加载轻量字段（load_only），大 JSON 列不在链路视图里下发；
        完整字段按 log_id 走单条详情接口。

        不在 SQL 侧 `ORDER BY id`：该查询若带上 params/result_data/
        variables/logs 等 JSON 大列，这些列会进入 filesort 排序元组，
        单行体量超过 sort_buffer_size(128K) 直接抛 1038。改为取出后按 id 在
        Python 侧排序，语义不变。
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ActionLogRecord)
                .options(load_only(*_SUMMARY_LOAD))
                .where(
                    ActionLogRecord.mid == str(mid),
                    ActionLogRecord.execution_id == execution_id,
                )
            )
            rows = list(result.all())
        rows.sort(key=lambda r: r.id or 0)
        return rows

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
            result = await session.exec(sa_delete(ActionLogRecord).where(*conditions))
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
            items.append(
                {
                    "action_id": action_id,
                    "action_name": action_name or "",
                    "total": cnt,
                    "success": success_cnt,
                    "failed": cnt - success_cnt,
                    "avg_execution_time": round(float(avg_time or 0.0), 4),
                }
            )

        return {
            "days": days,
            "total": total,
            "success": total_success,
            "failed": total - total_success,
            "items": items,
        }

    @staticmethod
    async def cleanup_expired() -> int:
        """按服务端默认保留天数清理过期日志，返回清理条数

        由 `app/setup.py` 的周期任务（`action_log_cleanup`）调度；
        内部消化所有异常（DB 不可用等），失败仅告警并返回 0，绝不打断调度器。
        """
        retention = settings.action_log_default_retention_days
        if not retention or retention <= 0:
            return 0
        cutoff = datetime.now() - timedelta(days=int(retention))
        deleted = 0
        try:
            async with DatabaseSessionManager.async_session() as session:
                res = await session.exec(
                    sa_delete(ActionLogRecord).where(
                        ActionLogRecord.started_at < cutoff
                    )
                )
                await session.commit()
                deleted = res.rowcount or 0
        except Exception as exc:  # noqa: BLE001 - 清理失败不打断调度
            logger.warning(f"[ActionLog] 过期日志清理失败: {exc}")
            return 0
        if deleted:
            logger.info(
                f"[ActionLog] 已清理 {deleted} 条超过 {retention} 天的操作日志"
            )
        return deleted


action_log_crud_svr = ActionLogCrudService()
