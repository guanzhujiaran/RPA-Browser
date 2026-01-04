from fastapi import Depends
import time
from app.models.RPA_browser.live_control_models import (
    PluginStatusResponse,
)
from app.models.response import StandardResponse, success_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.base import new_router

router = new_router()


@router.post(
    BrowserControlRouterPath.plugins_pause,
    response_model=StandardResponse[PluginStatusResponse],
)
async def pause_plugins(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    暂停插件自动操作

    暂停浏览器实例中所有插件的自动操作，启用手动操作模式。

    Args:
        request: 包含browser_id的请求

    Returns:
        dict: 操作结果，包含操作状态和消息
    """
    result = await LiveService.pause_plugins(auth_info.mid, browser_info.browser_id)
    return success_response(
        data=PluginStatusResponse(
            browser_id=browser_info.browser_id,
            plugins_paused=result.is_paused,
            paused_at=int(time.time()),
            reason="手动暂停",
            total_plugins=0,
            active_plugins=0,
        )
    )


@router.post(
    BrowserControlRouterPath.plugins_status,
    response_model=StandardResponse[PluginStatusResponse],
)
async def get_plugin_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    获取插件状态

    获取浏览器实例中插件的当前状态，包括是否暂停等信息。

    Returns:
        dict: 插件状态信息
    """
    result = LiveService.get_plugin_status(auth_info.mid, browser_info.browser_id)
    return success_response(
        data=PluginStatusResponse(
            browser_id=browser_info.browser_id,
            plugins_paused=result.plugins_paused,
            paused_at=result.paused_at,
            reason=result.reason,
            total_plugins=result.total_plugins,
            active_plugins=result.active_plugins,
        )
    )
