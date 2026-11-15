"""反馈管理端服务（管理后台 CRUD）。

只做数据访问与模型转换，鉴权与响应包装留在 controller 层。
列表查询跨提交者（不按 mid 归属过滤），游客反馈（``mid`` 为空）同样可见。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, func, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.database.feedback.models import RpaErrorReport
from app.models.feedback.feedback_requests import (
    FeedbackAdminCreateRequest,
    FeedbackAdminListRequest,
    FeedbackAdminUpdateRequest,
    FeedbackContext,
    FeedbackStatus,
)
from app.models.feedback.feedback_responses import FeedbackAdminItem


class FeedbackAdminService:
    """反馈管理端 CRUD

    所有方法显式接收 ``AsyncSession``（依赖注入），不自行创建会话，
    便于与 controller 层的事务 / 会话生命周期保持一致。
    """

    def _filters(self, request: FeedbackAdminListRequest) -> list[ColumnElement[bool]]:
        """按请求组装 WHERE 条件（空值不参与过滤）。"""
        conditions: list[ColumnElement[bool]] = []
        if request.mid is not None:
            conditions.append(RpaErrorReport.mid == int(request.mid))
        if request.status is not None:
            conditions.append(RpaErrorReport.status == request.status.value)
        if request.category is not None:
            conditions.append(RpaErrorReport.category == request.category.value)
        if request.uname:
            conditions.append(RpaErrorReport.uname.like(f"%{request.uname}%"))
        if request.keyword:
            pattern = f"%{request.keyword}%"
            conditions.append(
                or_(
                    RpaErrorReport.title.like(pattern),
                    RpaErrorReport.description.like(pattern),
                    RpaErrorReport.contact.like(pattern),
                )
            )
        return conditions

    async def count(self, session: AsyncSession, request: FeedbackAdminListRequest) -> int:
        """统计符合条件的反馈条数。"""
        stmt = select(func.count(RpaErrorReport.id))
        for condition in self._filters(request):
            stmt = stmt.where(condition)
        return (await session.exec(stmt)).one()

    async def list(
        self,
        session: AsyncSession,
        request: FeedbackAdminListRequest,
        skip: int,
        limit: int,
    ) -> list[RpaErrorReport]:
        """分页查询反馈（最新在前）。"""
        stmt = select(RpaErrorReport)
        for condition in self._filters(request):
            stmt = stmt.where(condition)
        stmt = (
            stmt.order_by(RpaErrorReport.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list((await session.exec(stmt)).all())

    async def get_by_report_id(
        self, session: AsyncSession, report_id: str
    ) -> RpaErrorReport | None:
        """按业务主键 ``report_id`` 查询单条反馈。"""
        stmt = select(RpaErrorReport).where(RpaErrorReport.report_id == report_id)
        return (await session.exec(stmt)).first()

    async def create(
        self, session: AsyncSession, request: FeedbackAdminCreateRequest
    ) -> RpaErrorReport:
        """管理端手工补录一条反馈。"""
        row = RpaErrorReport(
            report_id=uuid.uuid4().hex,
            mid=int(request.mid) if request.mid else None,
            uname=request.uname,
            contact=request.contact,
            category=request.category.value,
            title=request.title,
            description=request.description,
            context=json.dumps(
                (request.context or FeedbackContext()).model_dump(exclude_none=True),
                ensure_ascii=False,
            ),
            status=request.status.value,
            admin_remark=request.admin_remark,
        )
        if request.status != FeedbackStatus.pending:
            row.handled_at = datetime.now()
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

    async def update(
        self,
        session: AsyncSession,
        request: FeedbackAdminUpdateRequest,
        admin_mid: int,
    ) -> RpaErrorReport | None:
        """更新一条反馈，未显式传入的字段保持不变。"""
        row = await self.get_by_report_id(session, request.report_id)
        if row is None:
            return None

        fields_set = request.model_fields_set
        if request.category is not None:
            row.category = request.category.value
        if request.title is not None:
            row.title = request.title
        if request.description is not None:
            row.description = request.description
        # mid / uname / contact 允许显式置空（传 null 表示清空），故按字段是否传入判断
        if "mid" in fields_set:
            row.mid = int(request.mid) if request.mid else None
        if "uname" in fields_set:
            row.uname = request.uname
        if "contact" in fields_set:
            row.contact = request.contact
        if request.admin_remark is not None:
            row.admin_remark = request.admin_remark
        if request.context is not None:
            row.context = json.dumps(
                request.context.model_dump(exclude_none=True), ensure_ascii=False
            )
        if request.status is not None:
            row.status = request.status.value
            if request.status != FeedbackStatus.pending:
                row.handled_by = admin_mid
                row.handled_at = datetime.now()

        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

    async def delete_many(self, session: AsyncSession, report_ids: list[str]) -> int:
        """批量删除反馈，返回实际删除条数。"""
        stmt = select(RpaErrorReport).where(
            RpaErrorReport.report_id.in_(report_ids)  # type: ignore[attr-defined]
        )
        rows = list((await session.exec(stmt)).all())
        for row in rows:
            await session.delete(row)
        if rows:
            await session.commit()
        return len(rows)

    @staticmethod
    def to_item(row: RpaErrorReport) -> FeedbackAdminItem:
        """ORM 模型 → 管理端列表 / 详情项（context 解析成结构化对象）。"""
        context: FeedbackContext | None = None
        if row.context:
            try:
                context = FeedbackContext.model_validate_json(row.context)
            except Exception:  # noqa: BLE001 - 展示性数据，解析失败置空
                context = None
        return FeedbackAdminItem(
            id=row.id or 0,
            report_id=row.report_id,
            mid=str(row.mid) if row.mid is not None else None,
            uname=row.uname,
            contact=row.contact,
            category=row.category,
            title=row.title,
            description=row.description,
            context=context,
            status=row.status,
            admin_remark=row.admin_remark or "",
            handled_by=row.handled_by,
            handled_at=row.handled_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


feedback_admin_svr = FeedbackAdminService()

__all__ = ["FeedbackAdminService", "feedback_admin_svr"]
