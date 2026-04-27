"""用户浏览器默认设置路由模块

提供用户级别浏览器默认设置的 POST API（全部POST，避免缓存等问题）
- 获取用户的默认设置
- 创建或更新默认设置
- 删除默认设置
- 将默认设置应用到浏览器实例
"""

from fastapi import Depends
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.models.core.browser.info import (
    UserBrowserDefaultSettingRequest,
    UserBrowserDefaultSettingResponse,
    UserBrowserServerSideDefaultSetting,
)
from app.models.common.depends import (
    VerifyBrowserDependsReq,
    BrowserReqAuthInfo,
)
from app.services.RPA_browser.browser_db_service import BrowserDBService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.depends.security_depends import verify_browser_ownership
from app.utils.controller.router_path import gen_api_router
from app.models.router.all_routes import user_browser_default_settings_router
from app.models.router.router_prefix import UserBrowserDefaultSettingRouterPath


router = gen_api_router(user_browser_default_settings_router)


# ============ 用户浏览器默认设置 API（全部 POST） ============


class EmptyRequest(SQLModel):
    """空请求模型（占位符）"""

    pass


class GetSettingsRequest(SQLModel):
    """获取默认设置请求（占位符）"""

    pass


@router.post(UserBrowserDefaultSettingRouterPath.get_settings)
async def get_user_default_settings(
    req: GetSettingsRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[UserBrowserDefaultSettingResponse | None]:
    """
    获取用户的默认设置

    如果用户没有设置过默认设置，返回 null
    """
    settings = await BrowserDBService.get_user_default_settings(auth.mid, session)

    if not settings:
        return success_response(None)

    # 转换为响应模型
    response_data = settings.model_dump()
    return success_response(UserBrowserDefaultSettingResponse(**response_data))


@router.post(UserBrowserDefaultSettingRouterPath.create_or_update_settings)
async def create_or_update_user_default_settings(
    request: UserBrowserDefaultSettingRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[UserBrowserDefaultSettingResponse]:
    """
    创建或更新用户的默认设置

    如果用户已有默认设置，则更新；否则创建新的默认设置
    """
    result = await BrowserDBService.create_or_update_user_default_settings(
        mid=auth.mid,
        request=request,
        session=session,
    )
    return success_response(result)


class DeleteSettingsRequest(SQLModel):
    """删除默认设置请求（占位符）"""

    pass


@router.post(UserBrowserDefaultSettingRouterPath.delete_settings)
async def delete_user_default_settings(
    req: DeleteSettingsRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[bool]:
    """
    删除用户的默认设置

    成功删除返回 true，如果设置不存在返回 false
    """
    result = await BrowserDBService.delete_user_default_settings(auth.mid, session)
    return success_response(result)


class ApplySettingsRequest(VerifyBrowserDependsReq):
    """应用默认设置请求"""

    pass


@router.post(UserBrowserDefaultSettingRouterPath.apply_settings)
async def apply_default_settings_to_browser(
    browser_auth: BrowserReqAuthInfo = Depends(verify_browser_ownership),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[bool]:
    """
    将用户的默认设置应用到指定的浏览器实例

    将用户的默认设置（如代理、视口等）应用到指定的浏览器实例
    """
    result = await BrowserDBService.apply_default_settings_to_browser(
        browser_id=browser_auth.browser_id,
        mid=browser_auth.auth_info.mid,
        session=session,
    )

    if result:
        return success_response(True, msg="默认设置已成功应用到浏览器实例")
    else:
        return error_response(
            ResponseCode.INTERNAL_ERROR,
            "应用默认设置失败，请检查默认设置或浏览器实例是否存在",
        )


class GetServerDefaultsRequest(SQLModel):
    """获取服务端默认值请求（占位符）"""

    pass


@router.post(UserBrowserDefaultSettingRouterPath.get_server_user_setting_defaults)
async def get_server_default_settings() -> (
    StandardResponse[UserBrowserDefaultSettingResponse]
):
    """
    获取服务端预定义的默认设置

    返回服务器端的默认配置值，这些值用于在用户未设置时作为默认值
    """
    server_defaults = UserBrowserServerSideDefaultSetting(mid=-1)
    response_data = server_defaults.model_dump()
    return success_response(UserBrowserDefaultSettingResponse(**response_data))
