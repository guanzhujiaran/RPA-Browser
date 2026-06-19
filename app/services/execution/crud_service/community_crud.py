"""
社区 CRUD 服务（点赞和举报）
"""
from typing import List
from datetime import datetime
from sqlmodel import select, update, func

from app.models.database.workflow.models import (
    CompositeActionModel,
    UserWorkflow,
    UserPlugin,
    ResourceLike,
    ResourceReport,
    ResourceType,
    ReportReason,
)
from app.utils.depends.session_manager import DatabaseSessionManager


class CommunityCrudService:
    """社区 CRUD 服务"""

    @staticmethod
    async def toggle_like(
        mid: int,
        resource_type: ResourceType,
        resource_id: int,
    ) -> bool | None:
        async with DatabaseSessionManager.async_session() as session:
            if resource_type == ResourceType.CUSTOM_ACTION:
                result = await session.exec(
                    select(CompositeActionModel).where(CompositeActionModel.id == resource_id)
                )
                resource = result.first()
            elif resource_type == ResourceType.USER_WORKFLOW:
                result = await session.exec(
                    select(UserWorkflow).where(UserWorkflow.id == resource_id)
                )
                resource = result.first()
            elif resource_type == ResourceType.USER_PLUGIN:
                result = await session.exec(
                    select(UserPlugin).where(UserPlugin.id == resource_id)
                )
                resource = result.first()
            else:
                return None

            if not resource:
                return None

            # 检查是否已点赞
            like_result = await session.exec(
                select(ResourceLike).where(
                    (ResourceLike.mid == str(mid)) &
                    (ResourceLike.resource_type == resource_type) &
                    (ResourceLike.resource_id == resource_id)
                )
            )
            existing_like = like_result.first()

            if existing_like:
                await session.delete(existing_like)
                if resource_type == ResourceType.CUSTOM_ACTION:
                    await session.exec(
                        update(CompositeActionModel)
                        .where(CompositeActionModel.id == resource_id)
                        .values(likes_count=CompositeActionModel.likes_count - 1)
                    )
                elif resource_type == ResourceType.USER_WORKFLOW:
                    await session.exec(
                        update(UserWorkflow)
                        .where(UserWorkflow.id == resource_id)
                        .values(likes_count=UserWorkflow.likes_count - 1)
                    )
                elif resource_type == ResourceType.USER_PLUGIN:
                    await session.exec(
                        update(UserPlugin)
                        .where(UserPlugin.id == resource_id)
                        .values(likes_count=UserPlugin.likes_count - 1)
                    )
                await session.commit()
                return False

            like = ResourceLike(
                mid=str(mid),
                resource_type=resource_type,
                resource_id=resource_id,
            )
            session.add(like)

            if resource_type == ResourceType.CUSTOM_ACTION:
                await session.exec(
                    update(CompositeActionModel)
                    .where(CompositeActionModel.id == resource_id)
                    .values(likes_count=CompositeActionModel.likes_count + 1)
                )
            elif resource_type == ResourceType.USER_WORKFLOW:
                await session.exec(
                    update(UserWorkflow)
                    .where(UserWorkflow.id == resource_id)
                    .values(likes_count=UserWorkflow.likes_count + 1)
                )
            elif resource_type == ResourceType.USER_PLUGIN:
                await session.exec(
                    update(UserPlugin)
                    .where(UserPlugin.id == resource_id)
                    .values(likes_count=UserPlugin.likes_count + 1)
                )

            await session.commit()
            return True

    @staticmethod
    async def has_liked(
        mid: int, resource_type: ResourceType, resource_id: int
    ) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ResourceLike).where(
                    (ResourceLike.mid == mid) &
                    (ResourceLike.resource_type == resource_type) &
                    (ResourceLike.resource_id == resource_id)
                )
            )
            return result.first() is not None

    @staticmethod
    async def report(
        mid: int,
        resource_type: ResourceType,
        resource_id: int,
        reason: ReportReason = ReportReason.OTHER,
        description: str = "",
    ) -> bool | None:
        async with DatabaseSessionManager.async_session() as session:
            if resource_type == ResourceType.CUSTOM_ACTION:
                result = await session.exec(
                    select(CompositeActionModel).where(CompositeActionModel.id == resource_id)
                )
                resource = result.first()
            elif resource_type == ResourceType.USER_WORKFLOW:
                result = await session.exec(
                    select(UserWorkflow).where(UserWorkflow.id == resource_id)
                )
                resource = result.first()
            elif resource_type == ResourceType.USER_PLUGIN:
                result = await session.exec(
                    select(UserPlugin).where(UserPlugin.id == resource_id)
                )
                resource = result.first()
            else:
                return None

            if not resource:
                return None

            report_result = await session.exec(
                select(ResourceReport).where(
                    (ResourceReport.mid == mid) &
                    (ResourceReport.resource_type == resource_type) &
                    (ResourceReport.resource_id == resource_id)
                )
            )
            existing_report = report_result.first()

            if existing_report:
                return False

            report = ResourceReport(
                mid=mid,
                resource_type=resource_type,
                resource_id=resource_id,
                reason=reason,
                description=description,
                is_valid=True,
            )
            session.add(report)

            if resource_type == ResourceType.CUSTOM_ACTION:
                await session.exec(
                    update(CompositeActionModel)
                    .where(CompositeActionModel.id == resource_id)
                    .values(reports_count=CompositeActionModel.reports_count + 1)
                )
            elif resource_type == ResourceType.USER_WORKFLOW:
                await session.exec(
                    update(UserWorkflow)
                    .where(UserWorkflow.id == resource_id)
                    .values(reports_count=UserWorkflow.reports_count + 1)
                )
            elif resource_type == ResourceType.USER_PLUGIN:
                await session.exec(
                    update(UserPlugin)
                    .where(UserPlugin.id == resource_id)
                    .values(reports_count=UserPlugin.reports_count + 1)
                )

            await session.commit()
            return True

    @staticmethod
    async def update_report(
        report_id: int,
        mid: int,
        reason: int | None = None,
        description: str | None = None,
    ) -> bool | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ResourceReport).where(ResourceReport.id == report_id)
            )
            report = result.first()
            if not report:
                return None

            if str(report.mid) != str(mid):
                return False

            if not report.is_valid:
                return False

            if reason is not None:
                report.reason = reason
            if description is not None:
                report.description = description

            await session.commit()
            return True

    @staticmethod
    async def mark_report_invalid(
        report_id: int, admin_mid: int
    ) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ResourceReport).where(ResourceReport.id == report_id)
            )
            report = result.first()
            if not report:
                return False

            if not report.is_valid:
                return False

            report.is_valid = False
            report.reviewed_by_mid = admin_mid
            report.reviewed_at = datetime.now()

            if report.resource_type == ResourceType.CUSTOM_ACTION:
                await session.exec(
                    update(CompositeActionModel)
                    .where(CompositeActionModel.id == report.resource_id)
                    .values(reports_count=CompositeActionModel.reports_count - 1)
                )
            elif report.resource_type == ResourceType.USER_WORKFLOW:
                await session.exec(
                    update(UserWorkflow)
                    .where(UserWorkflow.id == report.resource_id)
                    .values(reports_count=UserWorkflow.reports_count - 1)
                )
            elif report.resource_type == ResourceType.USER_PLUGIN:
                await session.exec(
                    update(UserPlugin)
                    .where(UserPlugin.id == report.resource_id)
                    .values(reports_count=UserPlugin.reports_count - 1)
                )

            await session.commit()
            return True

    @staticmethod
    async def list_reports(
        skip: int = 0,
        limit: int = 50,
        is_valid: bool | None = None,
        resource_type: ResourceType | None = None,
    ) -> List[ResourceReport]:
        async with DatabaseSessionManager.async_session() as session:
            query = select(ResourceReport)
            if is_valid is not None:
                query = query.where(ResourceReport.is_valid == is_valid)
            if resource_type is not None:
                query = query.where(ResourceReport.resource_type == resource_type)

            query = query.order_by(ResourceReport.created_at.desc())
            query = query.offset(skip).limit(limit)
            result = await session.exec(query)
            return result.all()

    @staticmethod
    async def count_reports(
        is_valid: bool | None = None,
        resource_type: ResourceType | None = None,
    ) -> int:
        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(ResourceReport.id))
            if is_valid is not None:
                query = query.where(ResourceReport.is_valid == is_valid)
            if resource_type is not None:
                query = query.where(ResourceReport.resource_type == resource_type)

            result = await session.exec(query)
            return result.one()


community_crud_svr = CommunityCrudService()