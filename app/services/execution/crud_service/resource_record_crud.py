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
    async def get_done_ids(
        mid: int | str,
        workflow_id: str,
        browser_id: int | str,
        resource_type: str,
        resource_ids: Iterable[Any] | None = None,
    ) -> set[str]:
        """取「已是终态」的资源 id 集合（success / skipped）。

        skipped 表示显式跳过（如活动已结束、页面已无该预约），与 success 一样
        属于终态，不再被取数捞起；failed 仍可重试（断点续跑）。

        Args:
            browser_id: 执行浏览器ID（去重按浏览器隔离）。
            resource_ids: 给定候选 id 时只在该集合内查（IN 收敛，推荐）；
                          为 None 时返回该工作流该浏览器该类型下全部终态 id（量大慎用）。
        """
        stmt = select(WorkflowResourceRecord.resource_id).where(
            WorkflowResourceRecord.mid == str(mid),
            WorkflowResourceRecord.workflow_id == int(workflow_id),
            WorkflowResourceRecord.browser_id == int(browser_id or 0),
            WorkflowResourceRecord.resource_type == resource_type,
            WorkflowResourceRecord.status.in_(  # type: ignore[attr-defined]
                [ResourceProcessStatus.SUCCESS, ResourceProcessStatus.SKIPPED]
            ),
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
        """从候选 id 中剔除终态项（success / skipped），返回待处理 id 列表。"""
        norm_ids = list(dict.fromkeys(_norm_id(x) for x in resource_ids))
        if not norm_ids:
            return []
        done = await ResourceRecordService.get_done_ids(
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
        status: ResourceProcessStatus | None = None,
        run_id: str | None = None,
        fail_reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """回写单个资源处理结果（幂等）。

        依赖唯一约束 uq_wf_resource_scope 做 MySQL ON DUPLICATE KEY UPDATE：
        已存在则更新状态/原因/最近处理时间，process_count 累加；
        first_processed_at 仅首次写入，不被覆盖。

        Args:
            status: 显式状态（success/failed/skipped），优先于 success。
        """
        await ResourceRecordService.bulk_upsert(
            mid,
            workflow_id,
            browser_id,
            resource_type,
            [resource_id],
            success,
            status=status,
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
        status: ResourceProcessStatus | None = None,
        run_id: str | None = None,
        fail_reason: str | None = None,
        extra_map: dict[str, dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        """批量回写同一处理结果的多个资源，返回写入条数。

        Args:
            status: 显式状态（success/failed/skipped），优先于 success。
            extra_map: 按 resource_id（字符串形式）提供各自的快照；优先于统一 extra。
            extra: 所有项统一的快照；extra_map 未命中某项时使用。
        """
        now = datetime.now()
        status = status or (
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
                    "workflow_id": int(workflow_id),
                    "browser_id": int(browser_id or 0),
                    "resource_type": resource_type,
                    "resource_id": raw,
                    "run_id": run_id,
                    "status": status,
                    # 只有 success 才丢弃原因：skipped 是「终态跳过」，原因（如
                    # 「已点赞，视为已参与」「活动已结束」）恰恰是排查误记的唯一线索，
                    # 之前按 success 判断会把 skipped 的原因一起抹掉。
                    "fail_reason": (
                        None
                        if status == ResourceProcessStatus.SUCCESS
                        else (fail_reason or None)
                    ),
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

    # ---------------- 单条读取 / 手工增删（前端「已处理数据」维护） ----------------

    @staticmethod
    async def get_record(
        mid: int | str,
        workflow_id: int,
        browser_id: int | str,
        resource_type: str,
        resource_id: Any,
    ) -> WorkflowResourceRecord | None:
        """按唯一键（mid + workflow + browser + type + resource_id）取单条记录。"""
        async with DatabaseSessionManager.async_session() as session:
            return (
                await session.exec(
                    select(WorkflowResourceRecord).where(
                        WorkflowResourceRecord.mid == str(mid),
                        WorkflowResourceRecord.workflow_id == int(workflow_id),
                        WorkflowResourceRecord.browser_id == int(browser_id or 0),
                        WorkflowResourceRecord.resource_type == resource_type,
                        WorkflowResourceRecord.resource_id == _norm_id(resource_id),
                    )
                )
            ).first()

    @staticmethod
    async def delete_records(
        mid: int | str,
        workflow_id: int,
        *,
        ids: Sequence[int] | None = None,
        browser_id: int | None = None,
        resource_type: str | None = None,
        status: ResourceProcessStatus | None = None,
        resource_id: str | None = None,
        run_id: str | None = None,
        dry_run: bool = False,
    ) -> int:
        """删除已处理资源记录（使这些资源重新参与取数），返回删除条数。

        与 :meth:`reset` 的区别：`browser_id` 可省略（前端「已处理数据」是按工作流看
        整体），并支持按记录主键 / 运行ID / 业务主键删除，用于清理误记数据。

        安全约束（防止一次点错清空整个工作流的去重记录）：
            - 除 mid + workflow_id 外，**必须**至少再给一个过滤条件；
            - 支持 ``dry_run`` 只统计不删除，供前端二次确认。

        Args:
            ids: 记录主键（列表页勾选删除）。
            run_id: 所属运行ID（清理「某一轮运行」写入的记录）。
            resource_id: 业务主键，**精确匹配**（与列表页的模糊搜索语义不同）。
        """
        conditions = [
            WorkflowResourceRecord.mid == str(mid),
            WorkflowResourceRecord.workflow_id == int(workflow_id),
        ]
        if ids:
            conditions.append(
                WorkflowResourceRecord.id.in_(list(ids))  # type: ignore[attr-defined]
            )
        if browser_id is not None:
            conditions.append(
                WorkflowResourceRecord.browser_id == int(browser_id or 0)
            )
        if resource_type:
            conditions.append(
                WorkflowResourceRecord.resource_type == resource_type
            )
        if status is not None:
            conditions.append(WorkflowResourceRecord.status == status)
        if resource_id:
            conditions.append(
                WorkflowResourceRecord.resource_id == _norm_id(resource_id)
            )
        if run_id:
            conditions.append(WorkflowResourceRecord.run_id == run_id)

        if len(conditions) <= 2:
            raise ValueError(
                "缺少删除条件：除工作流外至少指定 ids / browser_id / resource_type / "
                "status / resource_id / run_id 之一"
            )

        async with DatabaseSessionManager.async_session() as session:
            if dry_run:
                total = await session.scalar(
                    sa.select(sa_func.count())
                    .select_from(WorkflowResourceRecord)
                    .where(*conditions)
                )
                return int(total or 0)
            result = await session.execute(
                sa_delete(WorkflowResourceRecord).where(*conditions)  # type: ignore[arg-type]
            )
            await session.commit()
            return int(result.rowcount or 0)

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
            WorkflowResourceRecord.workflow_id == int(workflow_id),
            WorkflowResourceRecord.browser_id == int(browser_id or 0),
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
            WorkflowResourceRecord.workflow_id == int(workflow_id),
            WorkflowResourceRecord.browser_id == int(browser_id or 0),
        ]
        if resource_type is not None:
            conditions.append(
                WorkflowResourceRecord.resource_type == resource_type
            )
        if status is not None:
            conditions.append(WorkflowResourceRecord.status == status)
        if resource_id:
            conditions.append(
                WorkflowResourceRecord.resource_id.like(f"%{resource_id}%")  # type: ignore[attr-defined]
            )
        if run_id:
            conditions.append(WorkflowResourceRecord.run_id == run_id)
        if process_count_min is not None:
            conditions.append(
                WorkflowResourceRecord.process_count >= int(process_count_min)
            )
        if process_count_max is not None:
            conditions.append(
                WorkflowResourceRecord.process_count <= int(process_count_max)
            )
        if processed_from is not None:
            conditions.append(
                WorkflowResourceRecord.last_processed_at >= processed_from
            )
        if processed_to is not None:
            conditions.append(
                WorkflowResourceRecord.last_processed_at <= processed_to
            )

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

    @staticmethod
    async def list_records(
        mid: int | str,
        workflow_id: int,
        pagination: BasePaginationReq,
        *,
        browser_id: int | None = None,
        resource_type: str | None = None,
        status: ResourceProcessStatus | None = None,
        resource_id: str | None = None,
        run_id: str | None = None,
        process_count_min: int | None = None,
        process_count_max: int | None = None,
        processed_from: datetime | None = None,
        processed_to: datetime | None = None,
    ) -> BasePaginationResp[WorkflowResourceRecord]:
        """分页查询某工作流的已处理资源记录（按最近处理时间倒序）。

        与 `list_history` 的区别：浏览器可选——前端「已处理数据」看的是整个工作流，
        不强制先选浏览器（记录本身仍按浏览器隔离存储）。
        """
        conditions = [
            WorkflowResourceRecord.mid == str(mid),
            WorkflowResourceRecord.workflow_id == int(workflow_id),
        ]
        if browser_id is not None:
            conditions.append(WorkflowResourceRecord.browser_id == int(browser_id))
        if resource_type:
            conditions.append(
                WorkflowResourceRecord.resource_type == resource_type
            )
        if status is not None:
            conditions.append(WorkflowResourceRecord.status == status)
        if resource_id:
            conditions.append(
                WorkflowResourceRecord.resource_id.like(f"%{resource_id}%")  # type: ignore[attr-defined]
            )
        if run_id:
            conditions.append(WorkflowResourceRecord.run_id == run_id)
        if process_count_min is not None:
            conditions.append(
                WorkflowResourceRecord.process_count >= int(process_count_min)
            )
        if process_count_max is not None:
            conditions.append(
                WorkflowResourceRecord.process_count <= int(process_count_max)
            )
        if processed_from is not None:
            conditions.append(
                WorkflowResourceRecord.last_processed_at >= processed_from
            )
        if processed_to is not None:
            conditions.append(
                WorkflowResourceRecord.last_processed_at <= processed_to
            )

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
