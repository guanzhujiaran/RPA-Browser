"""报错 / 反馈管理端接口（仅管理员）。

用户侧 ``/browser/feedback/*`` 只做「提交 + 查自己的」；本模块面向管理后台，
对**全部用户（含未登录游客）**的反馈做完整 CRUD：

- 列表 / 详情：跨提交者查看（游客反馈 mid 为空同样可见）；
- 新增：管理端手工补录（如用户通过其它渠道反馈）；
- 更新：标题 / 描述 / 分类 / 状态 / 联系方式 / 上下文 / 处理备注；
- 删除：单条或批量。

安全边界：所有端点以 ``require_admin`` 守卫（root 或 be-message 判定的管理员），
非管理员一律 403。每次写操作记一条管理员审计日志。
"""

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from bili_common.deps.auth import AuthInfo
from bili_common.models.pagination import BasePaginationResp
from bili_common.models.response import (
    StandardResponse,
    error_response,
    success_response,
)
from bili_common.models.response_code import ResponseCode

from app.models.feedback.feedback_requests import (
    FeedbackAdminCreateRequest,
    FeedbackAdminDeleteRequest,
    FeedbackAdminGetRequest,
    FeedbackAdminListRequest,
    FeedbackAdminUpdateRequest,
)
from app.models.feedback.feedback_responses import (
    FeedbackAdminDeleteResp,
    FeedbackAdminItem,
)
from app.models.router.router_prefix import AdminFeedbackRouterPath
from app.services.admin_audit import log_admin_action
from app.services.feedback.feedback_admin_service import feedback_admin_svr
from app.utils.depends.admin_depends import require_admin
from app.utils.depends.session_manager import DatabaseSessionManager

router = APIRouter()  # 由 admin/__init__.py 聚合父路由统一提供前缀 /api/admin/rpa


@router.post(
    AdminFeedbackRouterPath.feedback_list,
    response_model=StandardResponse[BasePaginationResp[FeedbackAdminItem]],
    summary="获取全量反馈列表（管理端）",
)
async def list_feedbacks_admin(
    request: FeedbackAdminListRequest,
    auth: AuthInfo = Depends(require_admin),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[BasePaginationResp[FeedbackAdminItem]]:
    """跨提交者分页列出全部反馈（最新在前），支持状态 / 分类 / 提交者 / 关键词筛选"""
    skip = (request.page - 1) * request.per_page
    total = await feedback_admin_svr.count(session, request)
    rows = await feedback_admin_svr.list(session, request, skip, request.per_page)
    return success_response(
        BasePaginationResp[FeedbackAdminItem](
            page=request.page,
            per_page=request.per_page,
            total=total,
            items=[feedback_admin_svr.to_item(row) for row in rows],
        )
    )


@router.post(
    AdminFeedbackRouterPath.feedback_get,
    response_model=StandardResponse[FeedbackAdminItem],
    summary="获取单条反馈详情（管理端）",
)
async def get_feedback_admin(
    request: FeedbackAdminGetRequest,
    auth: AuthInfo = Depends(require_admin),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[FeedbackAdminItem]:
    """按 report_id 查看任意反馈详情（不做提交者归属校验）"""
    row = await feedback_admin_svr.get_by_report_id(session, request.report_id)
    if row is None:
        return error_response(ResponseCode.ERROR_REPORT_NOT_FOUND, "反馈不存在")
    return success_response(feedback_admin_svr.to_item(row))


@router.post(
    AdminFeedbackRouterPath.feedback_create,
    response_model=StandardResponse[FeedbackAdminItem],
    summary="新增一条反馈（管理端补录）",
)
async def create_feedback_admin(
    request: FeedbackAdminCreateRequest,
    auth: AuthInfo = Depends(require_admin),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[FeedbackAdminItem]:
    """管理端手工补录一条反馈（用于用户通过其它渠道反馈、需落库留痕的场景）"""
    row = await feedback_admin_svr.create(session, request)
    await log_admin_action(
        auth.mid,
        "feedback:create",
        "feedback",
        row.report_id,
        f"title={row.title}, status={row.status}, mid={row.mid}",
    )
    return success_response(feedback_admin_svr.to_item(row), msg="反馈已创建")


@router.post(
    AdminFeedbackRouterPath.feedback_update,
    response_model=StandardResponse[FeedbackAdminItem],
    summary="更新一条反馈（管理端）",
)
async def update_feedback_admin(
    request: FeedbackAdminUpdateRequest,
    auth: AuthInfo = Depends(require_admin),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[FeedbackAdminItem]:
    """更新任意反馈（未传字段保持不变）

    ``status`` 置为 ``resolved`` / ``ignored`` 时自动记录处理人与处理时间。
    """
    row = await feedback_admin_svr.update(session, request, auth.mid)
    if row is None:
        return error_response(ResponseCode.ERROR_REPORT_NOT_FOUND, "反馈不存在")
    await log_admin_action(
        auth.mid,
        "feedback:update",
        "feedback",
        row.report_id,
        f"title={row.title}, status={row.status}",
    )
    return success_response(feedback_admin_svr.to_item(row), msg="反馈已更新")


@router.post(
    AdminFeedbackRouterPath.feedback_delete,
    response_model=StandardResponse[FeedbackAdminDeleteResp],
    summary="删除反馈（管理端，支持批量）",
)
async def delete_feedback_admin(
    request: FeedbackAdminDeleteRequest,
    auth: AuthInfo = Depends(require_admin),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[FeedbackAdminDeleteResp]:
    """按 report_id 列表删除反馈（单条 / 勾选批量共用）"""
    deleted = await feedback_admin_svr.delete_many(session, request.report_ids)
    if deleted == 0:
        return error_response(ResponseCode.ERROR_REPORT_NOT_FOUND, "反馈不存在")
    await log_admin_action(
        auth.mid,
        "feedback:delete",
        "feedback",
        ",".join(request.report_ids),
        f"deleted={deleted}",
    )
    return success_response(
        FeedbackAdminDeleteResp(
            deleted=deleted, report_ids=request.report_ids
        ),
        msg=f"已删除 {deleted} 条反馈",
    )


__all__ = ["router"]
