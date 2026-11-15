"""
报错反馈路由

让用户（含 LLM 通过 MCP）把运行报错 / 改进建议直接提交给服主：
落库 + 弱依赖**站外**推送服主（见 docs/用户提交报错与实时推送服主计划书.md）。

落库是主链路、推送是弱依赖：提交成功即返回，推送失败只告警不阻塞。
站内通知不参与本链路（服主只需站外推送：邮件 / Webhook 等）。

落在用户级 router（/browser），因此会被 ``app/mcp/exposure.py`` 自动暴露成
MCP 工具（``rpa_feedback_submit`` / ``rpa_feedback_list``）。

**未登录也允许提交**：提交接口用可选登录依赖，游客可提交并留联系方式；
「我的反馈」列表仍需登录（游客没有归属数据）。
"""

from bili_common.models.response import StandardResponse, success_response
from fastapi import Depends
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.controller.v1.browser.base import new_feedback_router
from app.models.database.feedback.models import RpaErrorReport
from app.models.feedback.feedback_requests import (
    FeedbackContext,
    FeedbackListRequest,
    FeedbackSubmitRequest,
)
from app.models.feedback.feedback_responses import (
    FeedbackItem,
    FeedbackListResp,
    FeedbackSubmitResp,
)
from app.models.router.router_prefix import BrowserFeedbackRouterPath
from app.services.feedback.feedback_service import create_report, notify_owners
from app.utils.depends.mid_depends import (
    AuthInfo,
    get_auth_info_from_header,
    get_optional_auth_info_from_header,
)
from app.utils.depends.session_manager import DatabaseSessionManager

router = new_feedback_router()


@router.post(
    BrowserFeedbackRouterPath.feedback_submit,
    summary="提交报错 / 反馈",
    response_model=StandardResponse[FeedbackSubmitResp],
)
async def feedback_submit_router(
    request: FeedbackSubmitRequest,
    auth_info: AuthInfo | None = Depends(get_optional_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """提交一条运行报错 / 反馈给服主（登录 / 未登录均可）。

    把出错现场（分类、标题、描述 + 关联动作/工作流/运行/浏览器、错误摘要/详情、
    页面 URL 等上下文）落库，并按服主 NotificationConfig 做**站外**推送（邮件 / Webhook 等）。
    用于让服主及时知道哪里出错、哪里需要改进。用户通过 MCP 也可调用本工具上报。

    Args:
        request: 反馈内容（category/title/description 必填，其余可选上下文）
        auth_info: 登录态，从请求头自动获取；未登录为 None（可填写 contact 便于回访）
        session: 数据库会话

    Returns:
        FeedbackSubmitResp: report_id、提交者 mid / uname、提交时间、是否已推送服主。
        推送为弱依赖（失败不阻塞提交），notified=false 表示本次未发出推送但已落库。
    """
    report = await create_report(session, auth_info, request)
    notified = await notify_owners(report)
    return success_response(
        data=FeedbackSubmitResp(
            report_id=report.report_id,
            mid=str(report.mid) if report.mid is not None else None,
            uname=report.uname,
            created_at=report.created_at,
            notified=notified,
        )
    )


@router.post(
    BrowserFeedbackRouterPath.feedback_list,
    summary="查询自己的反馈列表",
    response_model=StandardResponse[FeedbackListResp],
)
async def feedback_list_router(
    request: FeedbackListRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """查询当前用户自己提交的报错 / 反馈列表（分页，按时间倒序）。

    供用户 / LLM 回查历史反馈与处理状态（pending / resolved / ignored）。

    Args:
        request: 分页参数（page / per_page）
        auth_info: 登录态信息（mid 从请求头获取，只返回该用户自己的反馈）
        session: 数据库会话

    Returns:
        FeedbackListResp: 反馈列表 + 总条数 + 分页信息。
    """
    mid = auth_info.mid
    total_stmt = select(func.count(RpaErrorReport.id)).where(
        RpaErrorReport.mid == mid
    )
    total = (await session.exec(total_stmt)).one()

    offset = (request.page - 1) * request.per_page
    list_stmt = (
        select(RpaErrorReport)
        .where(RpaErrorReport.mid == mid)
        .order_by(RpaErrorReport.created_at.desc())
        .offset(offset)
        .limit(request.per_page)
    )
    rows = (await session.exec(list_stmt)).all()

    items: list[FeedbackItem] = []
    for row in rows:
        context: FeedbackContext | None = None
        if row.context:
            try:
                context = FeedbackContext.model_validate_json(row.context)
            except Exception:  # noqa: BLE001 - 展示性数据，解析失败置 None
                context = None
        items.append(
            FeedbackItem(
                report_id=row.report_id,
                category=row.category,
                title=row.title,
                description=row.description,
                context=context,
                status=row.status,
                created_at=row.created_at,
            )
        )

    return success_response(
        data=FeedbackListResp(
            items=items,
            total=total,
            page=request.page,
            per_page=request.per_page,
        )
    )
