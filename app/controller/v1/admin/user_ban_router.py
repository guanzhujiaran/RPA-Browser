"""用户封禁管理 API

权限：root 或持有 `user:ban`（封禁/解封）、`user:ban-view`（查看）权限的 RPA 管理员。
生效范围：仅 RPA 服务（被封禁用户访问 RPA 接口时由中间件拦截），不影响评论/私信服务。
"""

from loguru import logger
from fastapi import APIRouter, Depends
from sqlmodel import select, func

from bili_common.deps.auth import AuthInfo
from bili_common.deps.permissions import UserPermission
from bili_common.models.response_code import ResponseCode
from bili_common.models.response import (
    StandardResponse,
    success_response,
    error_response,
)

from app.models.router.router_tag import RouterTag
from app.models.database.admin.models import UserBan
from app.models.system.user_ban import (
    BanUserRequest,
    LiftBanRequest,
    BanListRequest,
    BanListResponse,
    BanStatusRequest,
    BanStatusResponse,
    UserBanItemResp,
)
from app.services import user_ban as ban_service
from app.services.admin_audit import log_admin_action
from app.utils.depends.admin_depends import require_permission
from app.utils.depends.session_manager import DatabaseSessionManager

router = APIRouter(tags=[RouterTag.admin_management])


def _to_item(ban: UserBan) -> UserBanItemResp:
    return UserBanItemResp(
        id=ban.id or 0,
        mid=ban.mid,
        ban_type=ban.ban_type,
        status=ban.status,
        scope=ban.scope,
        reason=ban.reason,
        banned_by=ban.banned_by,
        banned_at=ban.banned_at,
        expired_at=ban.expired_at,
        lifted_by=ban.lifted_by,
        lifted_at=ban.lifted_at,
        lift_reason=ban.lift_reason,
        note=ban.note,
    )


@router.post("/ban/create", response_model=StandardResponse[UserBanItemResp])
async def ban_user(
    request: BanUserRequest,
    auth: AuthInfo = Depends(require_permission(UserPermission.USER_BAN)),
):
    """封禁用户（永久 / 临时），需 root 或 user:ban 权限"""
    try:
        if request.mid == auth.mid:
            return error_response(msg="不能封禁自己", code=ResponseCode.BAD_REQUEST)

        ban = await ban_service.ban_user(
            mid=request.mid,
            operator_mid=auth.mid,
            ban_type=request.ban_type,
            expired_at=request.expired_at,
            duration_minutes=request.duration_minutes,
            reason=request.reason,
            note=request.note,
        )
        await log_admin_action(
            auth.mid,
            "user:ban",
            "user",
            request.mid,
            f"type={ban.ban_type}, expired_at={ban.expired_at}, reason={request.reason}",
        )
        return success_response(data=_to_item(ban), msg="封禁成功")
    except ValueError as e:
        return error_response(msg=str(e), code=ResponseCode.BAD_REQUEST)
    except Exception as e:
        logger.error(f"❌ 封禁用户失败: {e}")
        return error_response(msg=f"封禁失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/ban/lift", response_model=StandardResponse[UserBanItemResp])
async def lift_user_ban(
    request: LiftBanRequest,
    auth: AuthInfo = Depends(require_permission(UserPermission.USER_BAN)),
):
    """解封用户，需 root 或 user:ban 权限"""
    try:
        ban = await ban_service.lift_ban(
            mid=request.mid, operator_mid=auth.mid, reason=request.reason
        )
        if ban is None:
            return error_response(msg="该用户当前未被封禁", code=ResponseCode.NOT_FOUND)
        await log_admin_action(
            auth.mid, "user:unban", "user", request.mid, f"reason={request.reason}"
        )
        return success_response(data=_to_item(ban), msg="解封成功")
    except Exception as e:
        logger.error(f"❌ 解封用户失败: {e}")
        return error_response(msg=f"解封失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/ban/list", response_model=StandardResponse[BanListResponse])
async def list_bans(
    request: BanListRequest,
    auth: AuthInfo = Depends(
        require_permission(UserPermission.USER_BAN, UserPermission.USER_BAN_VIEW)
    ),
):
    """分页查询封禁记录，需 root 或 user:ban / user:ban-view 权限"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            conditions = []
            if request.mid is not None:
                conditions.append(UserBan.mid == request.mid)
            if request.status:
                conditions.append(UserBan.status == request.status)

            count_stmt = select(func.count()).select_from(UserBan)
            list_stmt = select(UserBan)
            for cond in conditions:
                count_stmt = count_stmt.where(cond)
                list_stmt = list_stmt.where(cond)

            total = await session.exec(count_stmt)
            total_count = total.first() or 0

            result = await session.exec(
                list_stmt.order_by(UserBan.id.desc())  # type: ignore[union-attr]
                .offset((request.page - 1) * request.per_page)
                .limit(request.per_page)
            )
            items = [_to_item(b) for b in result.all()]

            return success_response(
                data=BanListResponse(
                    page=request.page,
                    per_page=request.per_page,
                    total=total_count,
                    items=items,
                )
            )
    except Exception as e:
        logger.error(f"❌ 查询封禁记录失败: {e}")
        return error_response(msg=f"查询失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/ban/status", response_model=StandardResponse[BanStatusResponse])
async def get_ban_status(
    request: BanStatusRequest,
    auth: AuthInfo = Depends(
        require_permission(UserPermission.USER_BAN, UserPermission.USER_BAN_VIEW)
    ),
):
    """查询指定用户当前封禁状态（临时封禁到期会自动置为失效）"""
    try:
        ban = await ban_service.get_active_ban(request.mid)
        if ban is None:
            return success_response(
                data=BanStatusResponse(mid=request.mid, is_banned=False)
            )
        return success_response(
            data=BanStatusResponse(
                mid=request.mid,
                is_banned=True,
                ban_type=ban.ban_type,
                reason=ban.reason,
                expired_at=ban.expired_at,
                banned_at=ban.banned_at,
                ban_id=ban.id,
            )
        )
    except Exception as e:
        logger.error(f"❌ 查询封禁状态失败: {e}")
        return error_response(msg=f"查询失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


__all__ = ["router"]
