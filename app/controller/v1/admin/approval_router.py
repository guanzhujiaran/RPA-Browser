"""RPA 操作审批 API

- 提交审批：任意登录用户
- 查看审批：管理员可看全部，普通用户仅看自己提交的
- 审核审批：仅管理员/root
"""
from datetime import datetime
from loguru import logger
from fastapi import APIRouter, Depends

from bili_common.deps.auth import AuthInfo, get_auth_info_from_header
from bili_common.models.response_code import ResponseCode
from bili_common.models.response import StandardResponse, success_response, error_response
from app.models.router.router_tag import RouterTag
from app.models.system.rpa_admin import (
    SubmitApprovalRequest,
    ApprovalItemResp,
    ApprovalListRequest,
    ApprovalListResponse,
    ReviewApprovalRequest,
)
from app.utils.depends.admin_depends import require_admin, get_admin_status
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.admin_audit import log_admin_action
from app.models.database.admin.models import ApprovalRequest
from sqlmodel import select, func

router = APIRouter(tags=[RouterTag.admin_management])


@router.post("/approval/submit", response_model=StandardResponse[ApprovalItemResp])
async def submit_approval(
    request: SubmitApprovalRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """提交 RPA 操作审批申请（任意登录用户）"""
    try:
        logger.info(f"📝 用户({auth.mid}) 提交审批: {request.resource_type}/{request.resource_id}/{request.action}")
        async with DatabaseSessionManager.async_session() as session:
            approval = ApprovalRequest(
                submitter_mid=auth.mid,
                resource_type=request.resource_type,
                resource_id=request.resource_id,
                action=request.action,
                title=request.title,
                description=request.description,
                status="pending",
            )
            session.add(approval)
            await session.commit()
            await session.refresh(approval)
            return success_response(
                data=_to_approval_item(approval), msg="审批申请已提交"
            )
    except Exception as e:
        logger.error(f"❌ 提交审批失败: {e}")
        return error_response(msg=f"提交失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/approval/list", response_model=StandardResponse[ApprovalListResponse])
async def list_approvals(
    request: ApprovalListRequest,
    status_obj=Depends(get_admin_status),
):
    """查看审批列表

    管理员/root 查看全部；普通用户仅查看自己提交的（防止越权看到他人数据）。
    """
    try:
        async with DatabaseSessionManager.async_session() as session:
            stmt = select(ApprovalRequest)
            # 普通用户只能看自己的；管理员看全部
            if not status_obj.is_admin:
                stmt = stmt.where(ApprovalRequest.submitter_mid == status_obj.mid)
            if request.status:
                stmt = stmt.where(ApprovalRequest.status == request.status)
            if request.resource_type:
                stmt = stmt.where(ApprovalRequest.resource_type == request.resource_type)

            # 带相同过滤条件的计数
            count_stmt = select(func.count()).select_from(ApprovalRequest)
            if not status_obj.is_admin:
                count_stmt = count_stmt.where(ApprovalRequest.submitter_mid == status_obj.mid)
            if request.status:
                count_stmt = count_stmt.where(ApprovalRequest.status == request.status)
            if request.resource_type:
                count_stmt = count_stmt.where(ApprovalRequest.resource_type == request.resource_type)
            total_count = (await session.exec(count_stmt)).first() or 0

            result = await session.exec(
                stmt.order_by(ApprovalRequest.id.desc())
                .offset((request.page - 1) * request.per_page)
                .limit(request.per_page)
            )
            items = [_to_approval_item(a) for a in result.all()]
            return success_response(
                data=ApprovalListResponse(
                    page=request.page, per_page=request.per_page, total=total_count, items=items
                )
            )
    except Exception as e:
        logger.error(f"❌ 查询审批失败: {e}")
        return error_response(msg=f"查询失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/approval/review", response_model=StandardResponse[ApprovalItemResp])
async def review_approval(
    request: ReviewApprovalRequest,
    auth: AuthInfo = Depends(require_admin),
):
    """审核审批（仅管理员/root）"""
    try:
        if request.status not in ("approved", "rejected"):
            return error_response(msg="status 必须为 approved 或 rejected", code=ResponseCode.BAD_REQUEST)
        logger.info(f"✅ 管理员({auth.mid}) 审核审批 #{request.approval_id} -> {request.status}")
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ApprovalRequest).where(ApprovalRequest.id == request.approval_id)
            )
            approval = result.first()
            if approval is None:
                return error_response(msg="审批单不存在", code=ResponseCode.NOT_FOUND)
            if approval.status != "pending":
                return error_response(msg="该审批单已处理", code=ResponseCode.CONFLICT)
            approval.status = request.status
            approval.reviewer_mid = auth.mid
            approval.review_note = request.review_note
            approval.reviewed_at = datetime.now()
            await session.commit()
            await session.refresh(approval)
            await log_admin_action(auth.mid, "approval:review", "approval", request.approval_id, f"status={request.status}, note={request.review_note}")
            return success_response(data=_to_approval_item(approval), msg="审核完成")
    except Exception as e:
        logger.error(f"❌ 审核审批失败: {e}")
        return error_response(msg=f"审核失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


def _to_approval_item(a: ApprovalRequest) -> ApprovalItemResp:
    return ApprovalItemResp(
        id=a.id,
        submitter_mid=a.submitter_mid,
        resource_type=a.resource_type,
        resource_id=a.resource_id,
        action=a.action,
        title=a.title,
        description=a.description,
        status=a.status,
        reviewer_mid=a.reviewer_mid,
        review_note=a.review_note,
        created_at=a.created_at,
        reviewed_at=a.reviewed_at,
    )
