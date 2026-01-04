from fastapi import Depends
from app.models.RPA_browser.rpa_operation_models import (
    ExecuteJsResponse,
    SecurityCheckParams,
    SecurityCheckResult,
)
from app.models.RPA_browser.simplified_models import SimplifiedJavaScriptExecuteWithParamsRequest
from app.models.response import StandardResponse, success_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.services.RPA_browser.security_service import SecurityChecker, JavaScriptSandbox
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.security_base import new_router

router = new_router()


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

    # 获取浏览器会话
    plugined_session = await LiveService.get_plugined_session(
        auth_info.mid, browser_info.browser_id, headless=True
    )
    page = await plugined_session.get_current_page()

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
    BrowserControlRouterPath.security_check,
    response_model=StandardResponse[SecurityCheckResult],
)
async def check_code_security(check_params: SecurityCheckParams):
    """
    检查JavaScript代码安全性

    对JavaScript代码进行安全分析，识别潜在的安全风险并给出评估结果。

    Args:
        check_params: 安全检查参数

    Returns:
        SecurityCheckResult: 安全检查结果
    """

    result = SecurityChecker.check_code_security(check_params)
    return success_response(data=result)
