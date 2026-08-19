"""
社区 CRUD 服务（举报）

点赞/收藏已迁移到 be-message（社区互动统一走 be-message），本服务仅保留举报，
举报归属各业务系统（RPA 举报记录存 RPA 库），通用逻辑可参考 bili-common。
"""
from typing import List
from datetime import datetime
from sqlmodel import select, update, func

from app.models.database.workflow.models import (
    CompositeActionModel,
    UserWorkflow,
    UserPlugin,
    ResourceReport,
    ResourceType,
    ReportReason,
    ReportDecision,
)
from app.utils.depends.session_manager import DatabaseSessionManager


class CommunityCrudService:
    """社区 CRUD 服务（举报）"""

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
        report_id: int, admin_mid: int, review_note: str = ""
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
            report.decision = ReportDecision.IGNORED
            report.review_note = review_note
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

    @staticmethod
    async def _load_resource(session, resource_type: ResourceType, resource_id: int):
        """按类型加载被举报资源（用于下架操作）"""
        if resource_type == ResourceType.CUSTOM_ACTION:
            result = await session.exec(
                select(CompositeActionModel).where(CompositeActionModel.id == resource_id)
            )
        elif resource_type == ResourceType.USER_WORKFLOW:
            result = await session.exec(
                select(UserWorkflow).where(UserWorkflow.id == resource_id)
            )
        elif resource_type == ResourceType.USER_PLUGIN:
            result = await session.exec(
                select(UserPlugin).where(UserPlugin.id == resource_id)
            )
        else:
            return None
        return result.first()

    @staticmethod
    async def _decrement_reports_count(session, resource) -> None:
        """资源被下架时同步递减其举报计数"""
        if isinstance(resource, CompositeActionModel):
            await session.exec(
                update(CompositeActionModel)
                .where(CompositeActionModel.id == resource.id)
                .values(reports_count=CompositeActionModel.reports_count - 1)
            )
        elif isinstance(resource, UserWorkflow):
            await session.exec(
                update(UserWorkflow)
                .where(UserWorkflow.id == resource.id)
                .values(reports_count=UserWorkflow.reports_count - 1)
            )
        elif isinstance(resource, UserPlugin):
            await session.exec(
                update(UserPlugin)
                .where(UserPlugin.id == resource.id)
                .values(reports_count=UserPlugin.reports_count - 1)
            )

    @staticmethod
    async def review_report(
        report_id: int,
        admin_mid: int,
        decision: ReportDecision,
        review_note: str = "",
    ) -> bool | None:
        """审核举报：ignore / warn / takedown

        - ignore:   标记无效，资源保持不变
        - warn:     警告被举报人（通知待私信系统建成后接入）
        - takedown: 下架资源（设为非公开，从社区隐藏）
        返回：None=举报不存在；False=已被处理；True=成功
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ResourceReport).where(ResourceReport.id == report_id)
            )
            report = result.first()
            if not report:
                return None
            if not report.is_valid:
                return False

            report.is_valid = False
            report.decision = decision
            report.review_note = review_note
            report.reviewed_by_mid = admin_mid
            report.reviewed_at = datetime.now()

            if decision == ReportDecision.TAKEDOWN:
                resource = await CommunityCrudService._load_resource(
                    session, report.resource_type, report.resource_id
                )
                if resource is not None:
                    resource.is_public = False
                    await CommunityCrudService._decrement_reports_count(session, resource)

            await session.commit()
            return True


community_crud_svr = CommunityCrudService()
