"""工作流主资源处理记录服务（通用去重 / 断点续跑）

封装 WorkflowResourceRecord 的读写，供执行引擎 / 动作层调用：
    - get_success_ids：批量取某工作流某类型下已成功的资源 id（去重主路径，一次查询）；
    - filter_pending：在给定 id 列表中剔除已成功项（取数后做差集）；
    - upsert_result / bulk_upsert：处理完成后幂等回写结果（依赖唯一约束）；
    - reset：清空记录，使其可被重新处理；
    - list_history：分页查询处理记录。

去重作用域：mid + workflow_id + resource_type + resource_id（按工作流隔离）。
仅 status=success 参与去重；failed/skipped 不影响下次捞取。

mid 一律以执行上下文透传，不信任外部传入的越权值；调用方需保证 mid 为当前登录用户。
"""

from datetime import datetime
from typing import Any, Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sa_func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlmodel import select

from app.models.base.base_sqlmodel import (
    BasePaginationReq,
    BasePaginationResp,
)
from app.models.database.workflow.resource_record import (
    ResourceProcessStatus,
    WorkflowResourceRecord,
)
from app.utils.depends.session_manager import DatabaseSessionManager


def _norm_id(v: Any) -> str:
    """资源主键统一转字符串存储（兼容 int / 复合 id / 第三方字符串 id）。"""
    return str(v)


class ResourceRecordService:
    """工作流主资源处理记录服务"""

    # ---------------- 读取（去重） ----------------

    @staticmethod
    async def get_success_ids(
        mid: int | str,
        workflow_id: str,
        browser_id: int | str,
        resource_type: str,
        resource_ids: Iterable[Any] | None = None,
    ) -> set[str]:
        """取已成功处理的资源 id 集合。

        Args:
            browser_id: 执行浏览器ID（去重按浏览器隔离）。
            resource_ids: 给定候选 id 时只在该集合内查（IN 收敛，推荐）；
                          为 None 时返回该工作流该浏览器该类型下全部成功 id（量大慎用）。
        """
        stmt = select(WorkflowResourceRecord.resource_id).where(
            WorkflowResourceRecord.mid == str(mid),
            WorkflowResourceRecord.workflow_id == workflow_id,
            WorkflowResourceRecord.browser_id == str(browser_id or ""),
            WorkflowResourceRecord.resource_type == resource_type,
            WorkflowResourceRecord.status == ResourceProcessStatus.SUCCESS,
        )
        ids_norm = [_norm_id(x) for x in (resource_ids or [])]
        if resource_ids is not None:
            if not ids_norm:
                return set()
            stmt = stmt.where(
                WorkflowResourceRecord.resource_id.in_(ids_norm)  # type: ignore
            )
        async with DatabaseSessionManager.async_session() as session:
            rows = await session.execute(stmt)
            return {r[0] for r in rows.all()}

    @staticmethod
    async def filter_pending(
        mid: int | str,
        workflow_id: str,
        browser_id: int | str,
        resource_type: str,
        resource_ids: Sequence[Any],
    ) -> list[str]:
        """从候选 id 中剔除已成功项，返回待处理 id 列表（保持原顺序、去重）。"""
        norm_ids = list(dict.fromkeys(_norm_id(x) for x in resource_ids))
        if not norm_ids:
            return []
        done = await ResourceRecordService.get_success_ids(
            mid, workflow_id, browser_id, resource_type, norm_ids
        )
        return [x for x in norm_ids if x not in done]

    # ---------------- 回写（幂等 upsert） ----------------

    @staticmethod
    async def upsert_result(
        mid: int | str,
        workflow_id: str,
        browser_id: int | str,
        resource_type: str,
        resource_id: Any,
        success: bool,
        *,
        run_id: str | None = None,
        fail_reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """回写单个资源处理结果（幂等）。

        依赖唯一约束 uq_wf_resource_scope 做 MySQL ON DUPLICATE KEY UPDATE：
        已存在则更新状态/原因/最近处理时间，process_count 累加；
        first_processed_at 仅首次写入，不被覆盖。
        """
        await ResourceRecordService.bulk_upsert(
            mid,
            workflow_id,
            browser_id,
            resource_type,
            [resource_id],
            success,
            run_id=run_id,
            fail_reason=fail_reason,
            extra=extra,
        )

    @staticmethod
    async def bulk_upsert(
        mid: int | str,
        workflow_id: str,
        browser_id: int | str,
        resource_type: str,
        resource_ids: Iterable[Any],
        success: bool,
        *,
        run_id: str | None = None,
        fail_reason: str | None = None,
        extra_map: dict[str, dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        """批量回写同一处理结果的多个资源，返回写入条数。

        Args:
            extra_map: 按 resource_id（字符串形式）提供各自的快照；优先于统一 extra。
            extra: 所有项统一的快照；extra_map 未命中某项时使用。
        """
        now = datetime.now()
        status = (
            ResourceProcessStatus.SUCCESS if success else ResourceProcessStatus.FAILED
        )
        rows: list[dict[str, Any]] = []
        for raw in dict.fromkeys(_norm_id(x) for x in resource_ids):
            item_extra = (
                extra_map.get(raw) if extra_map is not None else None
            )
            if item_extra is None:
                item_extra = extra
            rows.append(
                {
                    "mid": str(mid),
                    "workflow_id": workflow_id,
                    "browser_id": str(browser_id or ""),
                    "resource_type": resource_type,
                    "resource_id": raw,
                    "run_id": run_id,
                    "status": status,
                    "fail_reason": None if success else (fail_reason or None),
                    "process_count": 1,
                    "extra": item_extra,
                    "first_processed_at": now,
                    "last_processed_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        if not rows:
            return 0

        stmt = mysql_insert(WorkflowResourceRecord).values(rows)
        stmt = stmt.on_duplicate_key_update(
            run_id=stmt.inserted.run_id,
            status=stmt.inserted.status,
            fail_reason=stmt.inserted.fail_reason,
            extra=stmt.inserted.extra,
            last_processed_at=stmt.inserted.last_processed_at,
            updated_at=stmt.inserted.updated_at,
            process_count=WorkflowResourceRecord.process_count + 1,
        )
        async with DatabaseSessionManager.async_session() as session:
            await session.execute(stmt)
            await session.commit()
        return len(rows)

    # ---------------- 重置 / 历史 ----------------

    @staticmethod
    async def reset(
        mid: int | str,
        workflow_id: str,
        browser_id: int | str,
        resource_type: str | None = None,
        resource_ids: Iterable[Any] | None = None,
        *,
        status: ResourceProcessStatus | None = None,
    ) -> int:
        """删除指定浏览器（账号）上的处理记录，使其可被重新处理；返回删除条数。

        按浏览器隔离，browser_id 必传，不提供跨浏览器删除，避免误删其他账号记录。
        - 仅给 mid/workflow/browser：清空该浏览器上该工作流的全部记录；
        - 再给 resource_type：限定类型；
        - 再给 resource_ids：仅删指定资源；
        - status：仅删指定状态（如只清失败重试，默认不限）。
        """
        conditions = [
            WorkflowResourceRecord.mid == str(mid),
            WorkflowResourceRecord.workflow_id == workflow_id,
            WorkflowResourceRecord.browser_id == str(browser_id or ""),
        ]
        if resource_type is not None:
            conditions.append(
                WorkflowResourceRecord.resource_type == resource_type
            )
        if resource_ids is not None:
            ids_norm = [_norm_id(x) for x in dict.fromkeys(resource_ids)]
            if not ids_norm:
                return 0
            conditions.append(
                WorkflowResourceRecord.resource_id.in_(ids_norm)  # type: ignore
            )
        if status is not None:
            conditions.append(WorkflowResourceRecord.status == status)

        # conditions 为同构布尔子句列表（SQLModel 静态推断噪声，运行时正确）
        stmt = sa_delete(WorkflowResourceRecord).where(*conditions)  # type: ignore
        async with DatabaseSessionManager.async_session() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)

    @staticmethod
    async def list_history(
        mid: int | str,
        workflow_id: str,
        browser_id: int | str,
        pagination: BasePaginationReq,
        *,
        resource_type: str | None = None,
        status: ResourceProcessStatus | None = None,
    ) -> BasePaginationResp[WorkflowResourceRecord]:
        """分页查询某工作流在指定浏览器上的资源处理记录（按最近处理时间倒序）。

        历史按浏览器（账号）隔离，browser_id 必传，不提供跨浏览器视图。
        """
        conditions = [
            WorkflowResourceRecord.mid == str(mid),
            WorkflowResourceRecord.workflow_id == workflow_id,
            WorkflowResourceRecord.browser_id == str(browser_id or ""),
        ]
        if resource_type is not None:
            conditions.append(
                WorkflowResourceRecord.resource_type == resource_type
            )
        if status is not None:
            conditions.append(WorkflowResourceRecord.status == status)

        page = max(1, pagination.page)
        per_page = max(1, pagination.per_page)

        async with DatabaseSessionManager.async_session() as session:
            total = await session.scalar(
                select(sa_func.count())
                .select_from(WorkflowResourceRecord)
                .where(*conditions)
            )
            items = (
                await session.exec(  # type: ignore[attr-defined]
                    select(WorkflowResourceRecord)
                    .where(*conditions)
                    .order_by(
                        sa.desc(WorkflowResourceRecord.last_processed_at)  # type: ignore
                    )
                    .offset((page - 1) * per_page)
                    .limit(per_page)
                )
            ).all()

        return BasePaginationResp[WorkflowResourceRecord](
            page=page,
            per_page=per_page,
            total=int(total or 0),
            items=list(items),
        )
