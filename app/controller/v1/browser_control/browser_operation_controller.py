from fastapi import Depends, Response, BackgroundTasks
import time
from loguru import logger
from app.models.RPA_browser.browser_info_model import RPAResponse
from app.models.RPA_browser.rpa_operation_models import (
    ClickResponse,
    ExecuteJsResponse,
)
from app.models.RPA_browser.simplified_models import (
    SimplifiedLiveControlCommand,
    SimplifiedScreenshotRequest,
    SimplifiedNavigateRequest,
    SimplifiedJavaScriptExecuteRequest,
    SimplifiedBrowserClickRequest,
    SimplifiedJavaScriptExecuteWithParamsRequest,
)
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.services.RPA_browser.live_service import RPAOperationService
from app.services.RPA_browser.background_tasks import BackgroundTaskService
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.operation_base import new_router

router = new_router()


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


@router.post(BrowserControlRouterPath.navigate)
async def navigate_to_url(
    request: SimplifiedNavigateRequest,
    background_tasks: BackgroundTasks,
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
    # 从browser_info中获取mid
    mid = browser_info.mid
    
    # 添加后台任务
    background_tasks.add_task(
        BackgroundTaskService.navigate_to_url_background,
        mid,
        browser_info.browser_id,
        request.url,
    )

    return success_response(
        data={"message": "导航任务已提交到后台处理", "status": "submitted"}
    )


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
        mid,
        browser_info.browser_id,
        request.code,
    )

    return success_response(
        data={"message": "JavaScript执行任务已提交到后台处理", "status": "submitted"}
    )


@router.post(BrowserControlRouterPath.screenshot)
async def get_screenshot(
    request: SimplifiedScreenshotRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    获取浏览器实例的当前界面截图

    获取指定浏览器实例当前页面的截图，返回JPEG格式的图像数据。
    可自定义图片质量，用于快速查看浏览器当前状态或保存快照。

    Args:
        request: 包含browser_id和图片质量的请求

    Returns:
        Response: JPEG格式的图片数据

    Note:
        返回的图片不会缓存，每次都是最新的页面状态
    """
    # 获取浏览器会话
    plugined_session = await LiveService.get_plugined_session(
 auth_info.mid, browser_info.browser_id, is_create_browser=False
    )

    # 获取当前页面
    page = await plugined_session.get_current_page()

    # 检查页面是否已关闭
    if not page or page.is_closed():
        logger.error(f"Page is closed for browser_id={browser_info.browser_id}")
        return error_response(
            code=ResponseCode.PAGE_CLOSED, msg="浏览器页面已关闭，无法截图"
        )

    # 截图 - 增加超时时间到60秒，并捕获异常
    try:
        screenshot_bytes = await page.screenshot(
            type="jpeg",
            quality=request.quality,
            timeout=60000,  # 增加超时时间到60秒
            full_page=request.full_page,  # 使用请求参数控制是否全页面截图
        )
    except Exception as e:
        logger.error(f"Screenshot failed for browser_id={browser_info.browser_id}: {e}")
        # 检查是否为页面关闭错误
        if "Target page, context or browser has been closed" in str(e):
            return error_response(
                code=ResponseCode.PAGE_CLOSED, msg="浏览器页面已关闭，无法截图"
            )
        # 尝试使用更简单的截图参数重试一次
        try:
            screenshot_bytes = await page.screenshot(
                type="jpeg",
                quality=request.quality,
                timeout=10000,  # 更短的超时时间
                full_page=False,  # 重试时使用可视区域
                animations="disabled",  # 禁用动画
            )
        except Exception as retry_error:
            logger.error(f"Screenshot retry failed: {retry_error}")
            # 返回错误响应
            from app.models.response import error_response

            return error_response(
                code=ResponseCode.SCREENSHOT_FAILED, msg=f"截图失败: {str(e)}"
            )

    # 返回图片响应
    return Response(
        content=screenshot_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.post(
    BrowserControlRouterPath.click, response_model=StandardResponse[ClickResponse]
)
async def browser_click(
    request: SimplifiedBrowserClickRequest,
    background_tasks: BackgroundTasks,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    浏览器点击操作（后台任务模式）

    通过相对坐标执行浏览器点击操作，支持不同鼠标按钮和双击操作，使用后台任务处理点击操作。

    Args:
        request: 包含browser_id和点击参数的请求

    Returns:
        ClickResponse: 点击任务状态
    """
    # 添加后台任务
    background_tasks.add_task(
        BackgroundTaskService.click_background,
        mid,
        browser_info.browser_id,
        {
            "x": request.x,
            "y": request.y,
            "button": request.button,
            "double": request.double,
            "wait_after": request.wait_after,
        },
    )

    return success_response(
        data=ClickResponse(
            success=True,
            message="点击任务已提交到后台处理",
            coordinates={
                "relative": {"x": request.x, "y": request.y},
                "absolute": {"x": None, "y": None},  # 后台任务中会计算绝对坐标
            },
            timestamp=int(time.time()),
        )
    )


@router.post(
    BrowserControlRouterPath.execute, response_model=StandardResponse[ExecuteJsResponse]
)
async def execute_javascript_code(
    request: SimplifiedJavaScriptExecuteWithParamsRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    执行JavaScript代码

    在浏览器实例的当前页面中执行JavaScript代码，支持异步执行等待。

    Args:
        request: 包含browser_id和执行参数的请求

    Returns:
        ExecuteJsResponse: 执行结果
    """
    # 获取浏览器会话
    plugined_session = await LiveService.get_plugined_session(
 auth_info.mid, browser_info.browser_id, headless=True
    )
    page = await plugined_session.get_current_page()

    # 使用安全沙箱执行

    from app.services.RPA_browser.security_service import JavaScriptSandbox

    result = await JavaScriptSandbox.execute_with_safety(
        page=page,
        code=request.code,
        timeout=request.timeout,
        safety_check=True,
    )

    return success_response(
        data=ExecuteJsResponse(
            success=result.success,
            result=result.result,
            error=result.error,
            execution_time=result.execution_time,
        )
    )


@router.post(
    BrowserControlRouterPath.info,
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
    browser_info_data = await RPAOperationService.get_browser_info(plugined_session)

    # 添加手动操作状态信息
    manual_operation_info = {
        "is_paused": plugined_session.is_plugins_paused(),
        "message": (
            "手动操作模式" if plugined_session.is_plugins_paused() else "自动操作模式"
        ),
    }

    from app.models.RPA_browser.live_control_models import BrowserInfoResponse

    return success_response(
        data=BrowserInfoResponse(
            browser_id=browser_info.browser_id,
            pages=browser_info_data.get("pages", []),
            plugins=browser_info_data.get("plugins", []),
            connections=browser_info_data.get("connections", 0),
            manual_operation=manual_operation_info,
            session_info=browser_info_data.get("session_info", {}),
        )
    )


@router.post(
    BrowserControlRouterPath.status,
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
    from app.models.RPA_browser.live_control_models import BrowserStatus

    status = LiveService.get_browser_status(auth_info. auth_info.mid, browser_info.browser_id)

    if status:
        return success_response(data=status)
    else:
        return error_response(code=ResponseCode.NOT_FOUND, msg="浏览器会话未找到")


@router.post(
    BrowserControlRouterPath.operation_status,
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
    from app.models.RPA_browser.live_control_models import OperationStatusResponse

    result = LiveService.get_operation_status(auth_info. auth_info.mid, browser_info.browser_id)

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
