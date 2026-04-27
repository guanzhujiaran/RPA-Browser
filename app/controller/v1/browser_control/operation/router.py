from fastapi import Depends
from app.models.runtime.control import PluginStatusResponse
from app.models.response import StandardResponse, success_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.common.depends import BrowserReqInfo, BrowserReqAuthInfo
from ..base import new_operation_router

router = new_operation_router()


@router.post(
    BrowserControlRouterPath.plugins_pause,
    response_model=StandardResponse[PluginStatusResponse],
)
async def pause_plugins(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    暂停插件执行

    暂停浏览器实例中所有自动化插件的执行。

    Returns:
        PluginStatusResponse: 插件状态响应
    """
    result = await LiveService.pause_plugins(auth_info.mid, browser_info.browser_id)
    return success_response(data=result)


@router.post(
    BrowserControlRouterPath.plugins_status,
    response_model=StandardResponse[PluginStatusResponse],
    operation_id="get_plugin_status_operation",
)
async def get_plugin_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    获取插件状态

    获取浏览器实例中插件的当前状态，包括是否暂停等信息。

    Returns:
        PluginStatusResponse: 插件状态响应
    """
    result = LiveService.get_plugin_status(auth_info.mid, browser_info.browser_id)
    return success_response(data=result)
