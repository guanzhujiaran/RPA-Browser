"""RPA 管理员角色管理 API（仅 root 可操作）"""

from loguru import logger
from fastapi import APIRouter, Depends

from bili_common.deps.auth import AuthInfo, get_auth_info_from_header
from bili_common.models.response_code import ResponseCode
from bili_common.models.response import StandardResponse, success_response, error_response
from app.models.router.router_tag import RouterTag
from app.models.system.rpa_admin import (
    GrantAdminRequest,
    RevokeAdminRequest,
    AdminListRequest,
    AdminListResponse,
    RpaAdminItemResp,
)
from app.utils.depends.admin_depends import require_root, get_admin_status
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.admin_audit import log_admin_action
from app.models.database.admin.models import RpaAdmin
from app.models.system.rpa_admin import AdminStatusResponse
from sqlmodel import select, func

router = APIRouter(tags=[RouterTag.admin_management])


@router.post("/role/grant", response_model=StandardResponse[RpaAdminItemResp])
async def grant_admin(
    request: GrantAdminRequest,
    auth: AuthInfo = Depends(require_root),
):
    """授予用户 RPA 管理员身份（仅 root）"""
    try:
        logger.info(f"👑 Root({auth.mid}) 授予管理员: {request.mid}")
        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(RpaAdmin).where(RpaAdmin.mid == request.mid)
            )
            admin = existing.first()
            if admin is None:
                admin = RpaAdmin(
                    mid=request.mid,
                    granted_by=auth.mid,
                    permissions=request.permissions,
                    note=request.note,
                )
                session.add(admin)
            else:
                admin.granted_by = auth.mid
                admin.permissions = request.permissions
                admin.note = request.note
            await session.commit()
            await session.refresh(admin)
            if admin.id is None:
                raise Exception("管理员 ID 为空")
            item = RpaAdminItemResp(
                id=admin.id,
                mid=admin.mid,
                role=admin.role,
                granted_by=admin.granted_by,
                permissions=admin.permissions or ["*"],
                note=admin.note,
                created_at=admin.created_at,
            )
            await log_admin_action(auth.mid, "role:grant", "user", request.mid, f"permissions={request.permissions}, note={request.note}")
            return success_response(data=item, msg="已授予管理员权限")
    except Exception as e:
        logger.error(f"❌ 授予管理员失败: {e}")
        return error_response(
            msg=f"授予失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR
        )


@router.post("/role/revoke", response_model=StandardResponse[dict])
async def revoke_admin(
    request: RevokeAdminRequest,
    auth: AuthInfo = Depends(require_root),
):
    """撤销用户 RPA 管理员身份（仅 root）"""
    try:
        logger.info(f"👑 Root({auth.mid}) 撤销管理员: {request.mid}")
        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(RpaAdmin).where(RpaAdmin.mid == request.mid)
            )
            admin = existing.first()
            if admin is None:
                return error_response(
                    msg="该用户不是管理员", code=ResponseCode.NOT_FOUND
                )
            await session.delete(admin)
            await session.commit()
            await log_admin_action(auth.mid, "role:revoke", "user", request.mid)
            return success_response(data={"mid": request.mid}, msg="已撤销管理员权限")
    except Exception as e:
        logger.error(f"❌ 撤销管理员失败: {e}")
        return error_response(
            msg=f"撤销失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR
        )


@router.post("/role/list", response_model=StandardResponse[AdminListResponse])
async def list_admins(
    request: AdminListRequest,
    auth: AuthInfo = Depends(require_root),
):
    """列出所有 RPA 管理员（仅 root）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            total = await session.exec(select(func.count()).select_from(RpaAdmin))
            total_count = total.first() or 0

            result = await session.exec(
                select(RpaAdmin)
                .offset((request.page - 1) * request.per_page)
                .limit(request.per_page)
            )
            admins = result.all()

            items = [
                RpaAdminItemResp(
                    id=a.id,
                    mid=a.mid,
                    role=a.role,
                    granted_by=a.granted_by,
                    permissions=a.permissions or ["*"],
                    note=a.note,
                    created_at=a.created_at,
                )
                for a in admins
            ]
            return success_response(
                data=AdminListResponse(
                    page=request.page,
                    per_page=request.per_page,
                    total=total_count,
                    items=items,
                )
            )
    except Exception as e:
        logger.error(f"❌ 列出管理员失败: {e}")
        return error_response(
            msg=f"查询失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR
        )


@router.post("/role/me", response_model=StandardResponse[AdminStatusResponse])
async def role_me(status: AdminStatusResponse = Depends(get_admin_status)):
    """查询当前用户的角色状态（任意登录用户可访问，用于前端界面显隐）"""
    return success_response(data=status)
