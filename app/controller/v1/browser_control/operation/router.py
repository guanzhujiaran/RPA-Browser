from fastapi import Depends
from app.models.RPA_browser.browser_info_model import RPAResponse
from app.models.RPA_browser.live_control_models import (
    OperationStatusResponse,
    AutomationResumeResponse,
)
from app.models.RPA_browser.simplified_models import (
    SimplifiedLiveControlCommand,
    SimplifiedAutomationResumeRequest,
)
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.base import new_router

router = new_router()


@router.post(
    BrowserControlRouterPath.manual_stop,
    response_model=StandardResponse[AutomationResumeResponse],
)
async def stop_manual_operation(
    request: SimplifiedAutomationResumeRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    停止人工操作，恢复自动化

    结束人工操作模式，恢复自动化任务的执行。
    可选择强制恢复或提供恢复原因。

    Args:
        request: 包含browser_id和恢复参数的请求

    Returns:
        dict: 恢复结果，包含状态信息和恢复时间
    """
    result = await LiveService.resume_automation(auth_info.mid, browser_info.browser_id, request)
    return success_response(
        data=AutomationResumeResponse(
            success=result.success,
            browser_id=browser_info.browser_id,
            resumed_at=result.resume_time if result.success else 0,
            operation_id=None,
            message=result.message,
        )
    )


@router.post(
    BrowserControlRouterPath.operation_status,
    response_model=StandardResponse[OperationStatusResponse],
)
async def get_operation_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    获取操作状态（保持向后兼容）

    获取浏览器实例的当前操作状态，包括人工操作模式、优先级、连接数等信息。

    推荐使用 /browser/session/status 获取更完整的会话状态信息。

    Returns:
        dict: 操作状态详细信息
    """
    result = LiveService.get_operation_status(auth_info.mid, browser_info.browser_id)

    # 检查是否为错误状态
    if result.status == "not_found":
        return error_response(code=404, msg="会话不存在")  # 使用404表示资源不存在

    return success_response(
        data=OperationStatusResponse(
            browser_id=browser_info.browser_id,
            is_manual_mode=result.is_manual_mode,
            active_connections=result.active_connections,
            last_activity=result.last_activity,
            current_operation={},
            priority=result.current_priority,
            plugin_paused=result.is_manual_mode,
        )
    )


@router.post(
    BrowserControlRouterPath.control, response_model=StandardResponse[RPAResponse]
)
async def execute_browser_command(
    command: SimplifiedLiveControlCommand,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    执行浏览器控制命令

    通过HTTP接口执行浏览器操作命令，支持点击、填充、滚动、截图、JavaScript执行等操作。

    Args:
        command: 包含browser_id和控制命令的请求

    Returns:
        RPAResponse: 操作结果，包含成功状态和返回数据
    """
    # 执行浏览器命令
    result = await LiveService.execute_browser_command(
        auth_info.mid, browser_info.browser_id, command
    )
    return success_response(data=result)
