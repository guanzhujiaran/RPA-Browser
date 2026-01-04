from fastapi import Depends, BackgroundTasks
from app.models.RPA_browser.live_control_models import (
    BrowserInfoResponse,
    BrowserStatus,
)
from app.models.RPA_browser.simplified_models import (
    SimplifiedNavigateRequest,
    SimplifiedJavaScriptExecuteRequest,
)
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.services.RPA_browser.live_service import RPAOperationService
from app.services.RPA_browser.background_tasks import BackgroundTaskService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.base import new_router

router = new_router()


@router.post(
    BrowserControlRouterPath.info, response_model=StandardResponse[BrowserInfoResponse]
)
async def get_browser_info(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    获取浏览器详细信息

    获取浏览器实例的完整信息，包括页面列表、插件状态、连接状态等。

    Returns:
        dict: 浏览器详细信息
    """
    # 获取浏览器会话
    plugined_session = await LiveService.get_plugined_session(
        auth_info.mid, browser_info.browser_id, headless=True
    )
    browser_info = await RPAOperationService.get_browser_info(plugined_session)

    # 添加手动操作状态信息
    manual_operation_info = {
        "is_paused": plugined_session.is_plugins_paused(),
        "message": (
            "手动操作模式" if plugined_session.is_plugins_paused() else "自动操作模式"
        ),
    }

    return success_response(
        data=BrowserInfoResponse(
            browser_id=browser_info.browser_id,
            pages=browser_info.get("pages", []),
            plugins=browser_info.get("plugins", []),
            connections=browser_info.get("connections", 0),
            manual_operation=manual_operation_info,
            session_info=browser_info.get("session_info", {}),
        )
    )


@router.post(
    BrowserControlRouterPath.status, response_model=StandardResponse[BrowserStatus]
)
async def get_browser_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    获取浏览器状态

    获取浏览器实例的当前状态信息，包括连接数、最后活动时间等。

    Returns:
        BrowserStatus: 浏览器状态信息
    """
    status = LiveService.get_browser_status(auth_info.mid, browser_info.browser_id)

    if status:
        return success_response(data=status)
    else:
        return error_response(code=ResponseCode.NOT_FOUND, msg="浏览器会话未找到")


@router.post(BrowserControlRouterPath.navigate)
async def navigate_to_url(
    request: SimplifiedNavigateRequest,
    background_tasks: BackgroundTasks,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    导航到指定URL（后台任务模式）

    控制浏览器实例导航到指定的URL，使用后台任务处理导航操作。

    Args:
        request: 包含browser_id和目标URL的请求

    Returns:
        dict: 导航任务状态
    """
    # 添加后台任务
    background_tasks.add_task(
        BackgroundTaskService.navigate_to_url_background,
        auth_info.mid, browser_info.browser_id, request.url
    )
    
    return success_response(data={"message": "导航任务已提交到后台处理", "status": "submitted"})


@router.post(BrowserControlRouterPath.evaluate)
async def evaluate_javascript(
    request: SimplifiedJavaScriptExecuteRequest,
    background_tasks: BackgroundTasks,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    执行JavaScript代码（后台任务模式）

    在浏览器实例的当前页面中执行JavaScript代码，使用后台任务处理执行操作。

    Args:
        request: 包含browser_id和JavaScript代码的请求

    Returns:
        dict: 执行任务状态
    """
    # 添加后台任务
    background_tasks.add_task(
        BackgroundTaskService.evaluate_javascript_background,
        auth_info.mid, browser_info.browser_id, request.code
    )
    
    return success_response(data={"message": "JavaScript执行任务已提交到后台处理", "status": "submitted"})
