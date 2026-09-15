"""
安全校验依赖注入函数
用于验证浏览器ID是否属于特定用户MID
"""
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.common.exceptions.base_exception import (
    BrowserIdNotBeloneToUserException,
    FingerprintLimitExceededException,
)
from bili_common.deps.permissions import BizPermOp, has_biz_perm
from bili_common.models.interaction import InteractionBizTypeEnum
from bili_common.models.depends import (
    BrowserReqAuthInfo,
)
from app.services.RPA_browser.fingerprint.browser_fingerprint_service import BrowserFingerprintService
from app.services.RPA_browser.permission_config_service import PermissionConfigService
from app.utils.depends.mid_depends import (
    AuthInfo,
    UserRole,
    get_auth_info_from_header,
)
from app.utils.depends.session_manager import DatabaseSessionManager
from app.models.database.browser.info import UserBrowserInfo
from loguru import logger
from sqlmodel import select


async def _verify_browser_ownership_core(
    browser_id: int | str,
    auth_info: AuthInfo,
    session: AsyncSession,
) -> BrowserReqAuthInfo:
    """
    验证浏览器ID是否属于当前用户MID（核心逻辑）

    Args:
        browser_id: 浏览器ID
        auth_info: 认证信息
        session: 数据库会话

    Returns:
        BrowserReqAuthInfo: 验证通过的浏览器请求信息

    Raises:
        BrowserIdNotBeloneToUserException: 当浏览器不属于用户或不存在时抛出
    """

    # 验证浏览器指纹是否存在且属于当前用户
    fingerprint_info = await BrowserFingerprintService.read_fingerprint(
        browser_id=browser_id,
        mid=auth_info.mid,
        session=session,
    )

    if not fingerprint_info:
        raise BrowserIdNotBeloneToUserException(browser_id=browser_id)

    return BrowserReqAuthInfo(auth_info=auth_info, browser_id=browser_id)


async def verify_browser_ownership(
    browser_id: int | str,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> BrowserReqAuthInfo:
    """
    验证浏览器ID是否属于当前用户MID（GET 请求使用）

    Args:
        browser_id: 浏览器ID（从 query 参数获取）
        auth_info: 认证信息（从请求头获取）
        session: 数据库会话

    Returns:
        BrowserReqAuthInfo: 验证通过的浏览器请求信息

    Raises:
        HTTPException: 当浏览器不属于用户或不存在时抛出
    """
    return await _verify_browser_ownership_core(browser_id, auth_info, session)

def _is_browser_monitor_admin(auth_info: AuthInfo) -> bool:
    """是否具备「浏览器监管」身份：root，或持有 RPA_BROWSER 域 VIEW / BAN 位"""
    if auth_info.role == UserRole.ROOT.value:
        return True
    return has_biz_perm(
        auth_info.biz_perms,
        InteractionBizTypeEnum.RPA_BROWSER,
        BizPermOp.VIEW | BizPermOp.BAN,
    )


async def verify_browser_ownership_or_admin(
    browser_id: int | str,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> BrowserReqAuthInfo:
    """浏览器归属者 **或** 监管管理员可访问（WebRTC 只读拉流等监管场景）。

    - 浏览器属于当前用户：走原严格归属校验；
    - 不属于当前用户：仅当调用方为 root 或持有 `RPA_BROWSER` 域 VIEW / BAN 位时，
      只校验浏览器实例存在即放行（不校验归属），供审核员观看运行中的浏览器。

    普通用户行为与 `verify_browser_ownership` 完全一致。
    """
    try:
        return await _verify_browser_ownership_core(browser_id, auth_info, session)
    except BrowserIdNotBeloneToUserException:
        if not _is_browser_monitor_admin(auth_info):
            raise
        try:
            target_id = int(browser_id)
        except (TypeError, ValueError):
            raise BrowserIdNotBeloneToUserException(browser_id=browser_id)

        stmt = select(UserBrowserInfo).where(UserBrowserInfo.browser_id == target_id)
        exists = (await session.exec(stmt)).first()
        if exists is None:
            raise BrowserIdNotBeloneToUserException(browser_id=browser_id)

        logger.info(
            f"👨‍💼 管理员监管访问: admin_mid={auth_info.mid}, browser_id={browser_id}"
        )
        return BrowserReqAuthInfo(auth_info=auth_info, browser_id=browser_id)


async def verify_fingerprint_limit(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> AuthInfo:
    """
    验证用户是否已达到最大浏览器指纹数量限制

    Args:
        auth_info: 认证信息（从请求头获取）
        session: 数据库会话

    Returns:
        AuthInfo: 验证通过的认证信息

    Raises:
        FingerprintLimitExceededException: 当用户已达到最大指纹数量限制时抛出
    """
    # 获取当前等级允许的最大指纹数量
    max_fingerprints = await PermissionConfigService.get_max_fingerprints_by_level(
        auth_info.level, auth_info.role
    )

    # 获取当前用户的指纹数量
    current_count = await BrowserFingerprintService.count_fingerprint(auth_info.mid, session)

    # 检查是否超出限制
    if current_count >= max_fingerprints:
        raise FingerprintLimitExceededException(
            max_fingerprints=max_fingerprints)

    return auth_info
