from fastapi import Depends
import asyncio
import time
from app.models.RPA_browser.rpa_operation_models import (
    ClickResponse,
    ExecuteJsResponse,
)
from app.models.RPA_browser.simplified_models import (
    SimplifiedBrowserClickRequest,
    SimplifiedJavaScriptExecuteWithParamsRequest,
)
from app.models.response import StandardResponse, success_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.services.RPA_browser.security_service import JavaScriptSandbox, SecurityChecker
from app.models.RPA_browser.rpa_operation_models import SecurityCheckParams
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.base import new_router

router = new_router()


@router.post(
    BrowserControlRouterPath.click, response_model=StandardResponse[ClickResponse]
)
async def browser_click(
    request: SimplifiedBrowserClickRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    浏览器点击操作

    通过相对坐标执行浏览器点击操作，支持不同鼠标按钮和双击操作。

    Args:
        request: 包含browser_id和点击参数的请求

    Returns:
        ClickResponse: 点击操作结果
    """
    # 获取浏览器会话
    plugined_session = await LiveService.get_plugined_session(
        auth_info.mid, browser_info.browser_id, headless=True
    )
    page = await plugined_session.get_current_page()

    # 获取页面视口大小
    viewport = page.viewport_size
    if not viewport:
        # 如果没有设置视口，获取页面尺寸
        viewport = await page.evaluate(
            """
        () => ({
            width: window.innerWidth || document.documentElement.clientWidth,
            height: window.innerHeight || document.documentElement.clientHeight
        })
        """
        )

    # 计算绝对坐标
    abs_x = int(request.x * viewport["width"])
    abs_y = int(request.y * viewport["height"])

    # 执行点击操作
    if request.double:
        await page.dblclick(x=abs_x, y=abs_y, button=request.button)
    else:
        await page.click(x=abs_x, y=abs_y, button=request.button)

    # 等待指定时间
    if request.wait_after > 0:
        await asyncio.sleep(request.wait_after / 1000)

    return success_response(
        data=ClickResponse(
            success=True,
            message=f"{request.button}{'双击' if request.double else '点击'}执行成功",
            coordinates={
                "relative": {"x": request.x, "y": request.y},
                "absolute": {"x": abs_x, "y": abs_y},
                "viewport": viewport,
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
    BrowserControlRouterPath.safe_execute,
    response_model=StandardResponse[ExecuteJsResponse],
)
async def safe_execute_javascript(
    request: SimplifiedJavaScriptExecuteWithParamsRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    安全执行JavaScript代码

    在严格安全模式下执行JavaScript代码，代码会经过详细的安全检查和沙箱执行。

    Args:
        request: 包含browser_id和执行参数的请求

    Returns:
        ExecuteJsResponse: 执行结果
    """
    # 先进行安全检查
    check_params = SecurityCheckParams(
        code=request.code,
        strict_mode=True,
        timeout=min(request.timeout, 10000),  # 检查超时时间限制为10秒
    )

    check_result = SecurityChecker.check_code_security(check_params)

    if not check_result.safe_to_execute:
        return success_response(
            data=ExecuteJsResponse(
                success=False,
                result=None,
                error=f"代码安全检查失败: {check_result.level} 风险等级，评分 {check_result.score}/100",
                execution_time=0,
            )
        )
