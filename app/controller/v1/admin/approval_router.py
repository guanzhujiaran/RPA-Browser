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
from app.models.system.rpa_admin import (
    SubmitApprovalRequest,
    ApprovalItemResp,
    ApprovalListRequest,
    ApprovalListResponse,
    ReviewApprovalRequest,
    ApprovalCancelRequest,
    ApprovalDeleteRequest,
    ResourceSearchRequest,
    ResourceSearchItemResp,
    ResourceSearchResponse,
)
from app.models.database.workflow.models import CompositeActionModel, UserWorkflow, UserPlugin
from app.utils.depends.admin_depends import require_admin, get_admin_status
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.admin_audit import log_admin_action
from app.models.database.admin.models import ApprovalRequest
from sqlmodel import select, func

router = APIRouter()  # tag 由 admin/__init__.py 聚合父路由统一提供

# 审批单状态机：pending → approved/rejected；approved → rejected（过审核准撤回）；
# rejected → approved（驳回恢复）。与 be-message 动态审核的「撤回 / 恢复」语义对齐。
_APPROVAL_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"approved", "rejected"},
    "approved": {"rejected"},
    "rejected": {"approved"},
}


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
            # 仅查看本人申请：only_mine 强制只看当前请求人；非管理员本身就只能看自己
            if request.only_mine or not status_obj.is_admin:
                stmt = stmt.where(ApprovalRequest.submitter_mid == status_obj.mid)
            if request.status:
                stmt = stmt.where(ApprovalRequest.status == request.status)
            if request.resource_type:
                stmt = stmt.where(ApprovalRequest.resource_type == request.resource_type)

            # 带相同过滤条件的计数
            count_stmt = select(func.count()).select_from(ApprovalRequest)
            if request.only_mine or not status_obj.is_admin:
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
    """审核审批（仅管理员/root）

    状态机（对齐 be-message 动态审核「撤回 / 恢复」语义）：
    - pending  → approved / rejected（首次审核）
    - approved → rejected（过审核准撤回）
    - rejected → approved（驳回恢复）
    """
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
            allowed = _APPROVAL_TRANSITIONS.get(approval.status, set())
            if request.status not in allowed:
                return error_response(
                    msg=f"当前状态 {approval.status} 不允许变更为 {request.status}",
                    code=ResponseCode.CONFLICT,
                )
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


@router.post("/approval/cancel", response_model=StandardResponse[ApprovalItemResp])
async def cancel_approval(
    request: ApprovalCancelRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """撤回审批（仅可撤回自己提交且仍为待审核的审批）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ApprovalRequest).where(ApprovalRequest.id == request.approval_id)
            )
            approval = result.first()
            if approval is None:
                return error_response(msg="审批单不存在", code=ResponseCode.NOT_FOUND)
            if approval.submitter_mid != auth.mid:
                return error_response(msg="只能撤回自己提交的审批", code=ResponseCode.FORBIDDEN)
            if approval.status != "pending":
                return error_response(msg="仅待审核的审批可撤回", code=ResponseCode.CONFLICT)
            await session.delete(approval)
            await session.commit()
            logger.info(f"↩️ 用户({auth.mid}) 撤回审批 #{request.approval_id}")
            return success_response(data=_to_approval_item(approval), msg="审批已撤回")
    except Exception as e:
        logger.error(f"❌ 撤回审批失败: {e}")
        return error_response(msg=f"撤回失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/approval/delete", response_model=StandardResponse[ApprovalItemResp])
async def delete_approval(
    request: ApprovalDeleteRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """删除自己的审批记录（待审核、已通过、已驳回均可删除）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ApprovalRequest).where(ApprovalRequest.id == request.approval_id)
            )
            approval = result.first()
            if approval is None:
                return error_response(msg="审批单不存在", code=ResponseCode.NOT_FOUND)
            if approval.submitter_mid != auth.mid:
                return error_response(msg="只能删除自己提交的审批", code=ResponseCode.FORBIDDEN)
            await session.delete(approval)
            await session.commit()
            logger.info(f"🗑️ 用户({auth.mid}) 删除审批 #{request.approval_id}")
            return success_response(data=_to_approval_item(approval), msg="审批已删除")
    except Exception as e:
        logger.error(f"❌ 删除审批失败: {e}")
        return error_response(msg=f"删除失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/approval/resources", response_model=StandardResponse[ResourceSearchResponse])
async def search_resources(
    request: ResourceSearchRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """按名称搜索当前用户自己的资源，用于审批提交时的下拉选择"""
    model_map = {
        "action": (CompositeActionModel, "action_id"),
        "workflow": (UserWorkflow, "workflow_id"),
        "plugin": (UserPlugin, "plugin_id"),
    }
    if request.resource_type not in model_map:
        return error_response(msg="不支持的资源类型", code=ResponseCode.BAD_REQUEST)
    try:
        model_class, id_field = model_map[request.resource_type]
        keyword = (request.keyword or "").strip()
        stmt = select(model_class).where(model_class.mid == str(auth.mid))
        if keyword:
            stmt = stmt.where(func.lower(model_class.name).like(f"%{keyword.lower()}%"))
        stmt = stmt.order_by(model_class.created_at.desc()).limit(request.per_page)
        async with DatabaseSessionManager.async_session() as session:
            rows = (await session.exec(stmt)).all()
        items = [
            ResourceSearchItemResp(
                resource_type=request.resource_type,
                resource_id=getattr(r, id_field),
                name=r.name,
                created_at=r.created_at,
            )
            for r in rows
        ]
        return success_response(data=ResourceSearchResponse(items=items))
    except Exception as e:
        logger.error(f"❌ 查询资源失败: {e}")
        return error_response(msg=f"查询资源失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


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
