"""
RPA 管理权限依赖

提供：
- require_root:  仅 root 可访问（请求头 x-bili-role == root）
- require_admin: root 或 DB 中登记的 RPA 管理员可访问（越权即 403）
- require_permission: root 或持有指定细粒度权限的管理员可访问（权限以 DB 为准）
- get_admin_status: 任意登录用户查询自身角色状态（用于前端判断是否展示管理界面）
"""
from typing import Callable, Awaitable

from fastapi import Depends
from datetime import datetime
from sqlmodel import select

from bili_common.deps.auth import get_auth_info_from_header, AuthInfo, UserRole
from bili_common.models.response_code import ResponseCode
from app.models.common.exceptions.base_exception import BaseException as CustomBaseException
from app.utils.depends.session_manager import DatabaseSessionManager
from app.models.database.admin.models import RpaAdmin
from app.models.system.rpa_admin import AdminStatusResponse
from app.config import settings


class PermissionDeniedException(CustomBaseException):
    """权限不足（越权访问）"""

    code = ResponseCode.FORBIDDEN
    msg = "权限不足，需要管理员或 root 权限"


class ApprovalRequiredException(CustomBaseException):
    """操作需要先提交并通过审批"""

    code = ResponseCode.FORBIDDEN
    msg = "该操作需要先提交并通过审批"


async def require_root(auth: AuthInfo = Depends(get_auth_info_from_header)) -> AuthInfo:
    """仅 root 用户可通过，否则抛 403"""
    if auth.role != UserRole.ROOT.value:
        raise PermissionDeniedException()
    return auth


async def _is_rpa_admin(mid: int) -> bool:
    async with DatabaseSessionManager.async_session() as session:
        result = await session.exec(select(RpaAdmin).where(RpaAdmin.mid == mid))
        return result.first() is not None


async def require_admin(auth: AuthInfo = Depends(get_auth_info_from_header)) -> AuthInfo:
    """root 或 RPA 管理员可通过，否则抛 403（防止越权）"""
    if auth.role == UserRole.ROOT.value:
        return auth
    if await _is_rpa_admin(auth.mid):
        return auth
    raise PermissionDeniedException()


async def _get_admin_permissions(mid: int) -> list[str] | None:
    """查询管理员的细粒度权限列表；非管理员返回 None"""
    async with DatabaseSessionManager.async_session() as session:
        result = await session.exec(select(RpaAdmin).where(RpaAdmin.mid == mid))
        admin = result.first()
        if admin is None:
            return None
        return admin.permissions or []


def require_permission(
    *perms: str,
) -> Callable[..., Awaitable[AuthInfo]]:
    """依赖工厂：root 或「持有 perms 中任一权限」的管理员可通过

    权限以 RPA 数据库 `rpa_admin.permissions` 为准（不信任请求头），
    通过后会把真实权限回填到 `auth.permissions`，便于接口内做更细的判断。
    """

    async def _dep(auth: AuthInfo = Depends(get_auth_info_from_header)) -> AuthInfo:
        if auth.role == UserRole.ROOT.value:
            auth.permissions = ["*"]
            return auth
        db_perms = await _get_admin_permissions(auth.mid)
        if db_perms is None:
            raise PermissionDeniedException()
        auth.permissions = db_perms
        if not perms or auth.has_any_permission(*perms):
            return auth
        raise PermissionDeniedException()

    return _dep


async def get_admin_status(
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> AdminStatusResponse:
    """返回当前登录用户的角色状态（用于前端界面显隐控制）"""
    is_root = auth.role == UserRole.ROOT.value
    if is_root:
        return AdminStatusResponse(is_root=True, is_admin=True, permissions=["*"], mid=auth.mid)

    admin = None
    async with DatabaseSessionManager.async_session() as session:
        result = await session.exec(select(RpaAdmin).where(RpaAdmin.mid == auth.mid))
        admin = result.first()

    if admin is not None:
        return AdminStatusResponse(
            is_root=False,
            is_admin=True,
            permissions=admin.permissions or ["*"],
            mid=auth.mid,
        )
    return AdminStatusResponse(is_root=False, is_admin=False, permissions=[], mid=auth.mid)


async def assert_approved(resource_type: str, resource_id: str, action: str) -> None:
    """校验是否存在「已通过且未过期」的审批单，否则抛 ApprovalRequiredException。

    仅当 settings.require_approval_enabled 为 True 时生效（灰度/过渡期可关闭）。
    资源维度校验：只认 (resource_type, resource_id, action) 上存在 approved 审批单，
    不绑定提交人，即「该资源已获批准」即可执行。
    """
    if not settings.require_approval_enabled:
        return
    from app.models.database.admin.models import ApprovalRequest

    async with DatabaseSessionManager.async_session() as session:
        result = await session.exec(
            select(ApprovalRequest).where(
                ApprovalRequest.resource_type == resource_type,
                ApprovalRequest.resource_id == resource_id,
                ApprovalRequest.action == action,
                ApprovalRequest.status == "approved",
            )
        )
        approval = result.first()
        if approval is None:
            raise ApprovalRequiredException()
        if approval.expires_at is not None and approval.expires_at < datetime.now():
            raise ApprovalRequiredException()


__all__ = [
    "PermissionDeniedException",
    "ApprovalRequiredException",
    "require_root",
    "require_admin",
    "require_permission",
    "get_admin_status",
    "assert_approved",
]
