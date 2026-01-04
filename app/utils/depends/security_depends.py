"""
安全校验依赖注入函数
用于验证浏览器ID是否属于特定用户MID
"""

from app.models.exceptions.base_exception import (
    BrowserIdNotBeloneToUserException,
    BrowserIdIsNoneExeception,
    PluginIdIsNoneException,
    PluginIdNotBelongToUserException,
    FingerprintLimitExceededException,
)
from app.models.RPA_browser.browser_api_models import BrowserFingerprintQueryParams
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from app.services.RPA_browser.browser_db_service import BrowserDBService
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.RPA_browser.plugin_db_service import PluginDBService
from app.services.RPA_browser.permission_config_service import PermissionConfigService
from app.models.RPA_browser.depends_models import (
    VerifyBrowserDependsReq,
    BrowserReqInfo,
    VerifyPluginDependsReq,
    BrowserPluginReqInfo,
    VerifyFingerprintDependsReq,
)


async def verify_browser_ownership(
    body: VerifyBrowserDependsReq,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> BrowserReqInfo:
    """
    验证浏览器ID是否属于当前用户MID

    Args:
        body: 验证浏览器请求参数
        auth_info: 认证信息（从请求头获取）
        session: 数据库会话

    Returns:
        BrowserReqInfo: 验证通过的浏览器请求信息

    Raises:
        HTTPException: 当浏览器不属于用户或不存在时抛出
    """
    browser_id = body.browser_id
    if not browser_id:
        raise BrowserIdIsNoneExeception()

    # 验证浏览器指纹是否存在且属于当前用户
    fingerprint_info = await BrowserDBService.read_fingerprint(
        params=BrowserFingerprintQueryParams(id=browser_id),
        mid=auth_info.mid,
        session=session,
    )

    if not fingerprint_info:
        raise BrowserIdNotBeloneToUserException(browser_id=browser_id)

    return BrowserReqInfo(mid=auth_info.mid, browser_id=browser_id)


async def verify_plugin_ownership(
    body: VerifyPluginDependsReq,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> BrowserPluginReqInfo:
    """
    验证插件是否属于当前用户MID

    Args:
        body: 验证插件请求参数
        auth_info: 认证信息（从请求头获取）
        session: 数据库会话

    Returns:
        BrowserPluginReqInfo: 验证通过的插件请求信息

    Raises:
        PluginIdIsNoneException: 当插件ID为空时抛出
        PluginIdNotBelongToUserException: 当插件不属于用户或不存在时抛出
    """

    plugin_id = body.plugin_id
    if not plugin_id:
        raise PluginIdIsNoneException()

    # 验证插件是否存在且属于当前用户
    plugin_info = await PluginDBService.get_user_plugin(
        plugin_id=plugin_id, session=session
    )

    if not plugin_info:
        raise PluginIdNotBelongToUserException(plugin_id=plugin_id)

    return BrowserPluginReqInfo(
        mid=auth_info.mid, browser_id=body.browser_id, plugin_id=plugin_id
    )


async def verify_fingerprint_ownership(
    body: VerifyFingerprintDependsReq,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> BrowserReqInfo:
    """
    验证浏览器指纹是否属于当前用户MID

    Args:
        body: 验证指纹请求参数
        auth_info: 认证信息（从请求头获取）
        session: 数据库会话

    Returns:
        BrowserReqInfo: 验证通过的浏览器请求信息

    Raises:
        BrowserIdIsNoneExeception: 当浏览器ID为空时抛出
        BrowserIdNotBeloneToUserException: 当指纹不属于用户或不存在时抛出
    """
    browser_id = body.browser_id
    if not browser_id:
        raise BrowserIdIsNoneExeception()

    # 验证浏览器指纹是否存在且属于当前用户
    from app.models.RPA_browser.browser_info_model import BrowserFingerprintQueryParams

    fingerprint_info = await BrowserDBService.read_fingerprint(
        params=BrowserFingerprintQueryParams(id=browser_id),
        mid=auth_info.mid,
        session=session,
    )

    if not fingerprint_info:
        raise BrowserIdNotBeloneToUserException(browser_id=browser_id)

    return BrowserReqInfo(mid=auth_info.mid, browser_id=browser_id)


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
        auth_info.level
    )

    # 获取当前用户的指纹数量
    current_count = await BrowserDBService.count_fingerprint(auth_info.mid, session)

    # 检查是否超出限制
    if current_count >= max_fingerprints:
        raise FingerprintLimitExceededException(max_fingerprints=max_fingerprints)

    return auth_info
