"""
RPA 管理权限依赖

管理员身份统一由 be-message 服务裁决（权限数据存放于 be-message 的 `msg_admin` 表，
RPA 不再持有独立的 RpaAdmin 表，旧 `role/grant`、`role/revoke`、`role/list` 接口已删除）。

提供：
- require_root:          仅 root 可访问（请求头 x-bili-role == root）
- require_admin:         root 或 be-message 判定为消息管理端管理员（调用 GET /api/v1/message/admin/me）
- require_permission:    root 或持有指定资源域某操作位（biz_perms 按位检查，语义同 be-message）
- get_admin_status:      任意登录用户查询自身角色状态（转发 be-message /me 结果，用于前端显隐）
- assert_approved:       审批单校验（保持原逻辑，未改动）
"""
import time
from typing import Awaitable, Callable

from datetime import datetime

from fastapi import Depends
from sqlmodel import select

from bili_common.deps.auth import AuthInfo, UserRole, get_auth_info_from_header
from bili_common.deps.permissions import BizPermOp
from bili_common.models.admin_status import AdminStatusResponse
from bili_common.models.interaction import InteractionBizTypeEnum
from bili_common.models.response import StandardResponse
from bili_common.models.response_code import ResponseCode
from loguru import logger

from app.config import settings
from app.models.common.exceptions.base_exception import BaseException as CustomBaseException
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.http import httpx_client


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


# ---------------------------------------------------------------------------
# be-message /me 判定（带进程内 TTL 缓存，避免每个管理接口都打一次 be-message）
# ---------------------------------------------------------------------------

_ME_CACHE_TTL = 30  # 秒
_me_cache: dict[int, tuple[float, AdminStatusResponse]] = {}


async def _fetch_admin_status(mid: int) -> AdminStatusResponse:
    """调用 be-message `GET /api/v1/message/admin/me` 查询当前用户管理端状态。

    - 携带 `x-bili-mid` 头（网关已注入的可信登录态），be-message 据此返回本人状态；
    - 调用失败（be-message 不可用 / 超时）时 fail-closed 返回非管理员，避免越权。
    """
    now = time.monotonic()
    cached = _me_cache.get(mid)
    if cached and now - cached[0] < _ME_CACHE_TTL:
        return cached[1]

    status = AdminStatusResponse(is_root=False, is_admin=False, biz_perms={}, mid=mid)
    try:
        resp = await httpx_client.get(
            f"{settings.message_service_url.rstrip('/')}/api/v1/message/admin/me",
            headers={"x-bili-mid": str(mid)},
            timeout=3.0,
        )
        payload = StandardResponse[AdminStatusResponse].model_validate(resp.json())
        if payload.code == 0 and payload.data is not None:
            status = payload.data
        _me_cache[mid] = (now, status)
    except Exception as e:
        logger.warning(f"⚠️ be-message /me 查询失败(mid={mid})，按非管理员处理: {e}")
    return status


async def _get_admin_status(mid: int) -> AdminStatusResponse:
    return await _fetch_admin_status(mid)


async def require_admin(auth: AuthInfo = Depends(get_auth_info_from_header)) -> AuthInfo:
    """root 或 be-message 判定为管理员的用户可通过，否则抛 403（防止越权）"""
    if auth.role == UserRole.ROOT.value:
        return auth
    status = await _get_admin_status(auth.mid)
    if status.is_admin:
        return auth
    raise PermissionDeniedException()


def require_permission(
    biz: InteractionBizTypeEnum,
    op: BizPermOp | int = BizPermOp.VIEW,
) -> Callable[..., Awaitable[AuthInfo]]:
    """依赖工厂：root 或「对指定资源域持有指定操作位」的管理员可通过

    权限以 be-message `msg_admin.biz_perms`（per-biz 位掩码权限字）为准，
    经 `GET /api/v1/message/admin/me` 实时获取；root 恒通过。
    """

    async def _dep(auth: AuthInfo = Depends(get_auth_info_from_header)) -> AuthInfo:
        if auth.role == UserRole.ROOT.value:
            auth.biz_perms = {"*": 7}
            return auth
        status = await _get_admin_status(auth.mid)
        if not status.is_admin:
            raise PermissionDeniedException()
        auth.biz_perms = status.biz_perms or {}
        if auth.has_biz_perm(biz, int(op)):
            return auth
        raise PermissionDeniedException()

    return _dep


async def get_admin_status(
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> AdminStatusResponse:
    """返回当前登录用户的角色状态（用于前端界面显隐控制，数据来自 be-message /me）"""
    return await _get_admin_status(auth.mid)


async def assert_approved(resource_type: str, resource_id: str, action: str) -> None:
    """校验是否存在「已通过且未过期」的审批单，否则抛 ApprovalRequiredException。

    生效开关按 action 维度拆分：
    - action == "publish"：由 settings.require_publish_approval_enabled 控制（发布到社区）。
    - 其它 action（如 execute）：由 settings.require_approval_enabled 控制。
    默认 execute 关闭、publish 开启，互不影响。
    资源维度校验：只认 (resource_type, resource_id, action) 上存在 approved 审批单，
    不绑定提交人，即「该资源已获批准」即可执行。
    """
    if action == "publish":
        if not settings.require_publish_approval_enabled:
            return
    else:
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
