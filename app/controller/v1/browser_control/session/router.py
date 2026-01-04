from fastapi import Depends, BackgroundTasks
import time
from app.models.RPA_browser.live_control_models import (
    HeartbeatResponse,
    CreateSessionResponse,
    BrowserSessionStatus,
)
from app.models.RPA_browser.simplified_models import (
    SimplifiedHeartbeatRequest,
    SimplifiedCreateSessionRequest,
)
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.base import new_router

router = new_router()


@router.post(
    BrowserControlRouterPath.heartbeat,
    response_model=StandardResponse[HeartbeatResponse],
)
async def send_heartbeat(
    request: SimplifiedHeartbeatRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    发送心跳信号

    客户端定期发送心跳以保持连接活跃，防止会话被清理。
    支持多客户端同时连接同一个浏览器实例。

    Args:
        request: 心跳数据请求

    Returns:
        HeartbeatResponse: 心跳响应，包含下次心跳间隔和状态信息
    """
    # 处理心跳
    response = await LiveService.handle_heartbeat(auth_info.mid, browser_info.browser_id, request)

    # 根据心跳结果决定返回成功还是错误响应
    if response.success:
        return success_response(data=response)
    else:
        # 会话不存在，返回错误响应
        return error_response(
            code=404,  # 使用404表示资源未找到
            msg=f"浏览器会话不存在: {response.status}",
            data=response,
        )


@router.post(
    BrowserControlRouterPath.session_create,
    response_model=StandardResponse[CreateSessionResponse],
)
async def create_browser_session(
    request: SimplifiedCreateSessionRequest,
    background_tasks: BackgroundTasks,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    创建浏览器会话

    独立的会话创建接口，与心跳机制完全解耦。
    如果浏览器未运行，将创建任务添加到后台任务中异步执行，立即返回响应。

    Args:
        request: 会话创建参数请求
        background_tasks: FastAPI 后台任务

    Returns:
        CreateSessionResponse: 会话创建结果
    """
    # 检查会话是否已存在
    session_key = f"{auth_info.mid}_{browser_info.browser_id}"

    if session_key in LiveService.browser_sessions:
        # 会话已存在，返回现有会话信息
        entry = LiveService.browser_sessions[session_key]
        created_at = getattr(entry, "created_at", entry.last_activity)
        expires_at = getattr(entry, "expires_at", None)

        response_data = CreateSessionResponse(
            success=True,
            session_id=session_key,
            browser_started=True,
            created_at=created_at,
            expires_at=expires_at,
            message="会话已存在，返回现有会话信息",
        )
        return success_response(data=response_data)

    # 会话不存在，将创建任务添加到后台
    background_tasks.add_task(
        LiveService.create_browser_session_background,
        auth_info.mid,
        browser_info.browser_id,
        request,
    )

    # 立即返回响应，表示任务已启动
    current_time = int(time.time())
    response_data = CreateSessionResponse(
        success=True,
        session_id=session_key,
        browser_started=False,  # 还未启动，在后台创建中
        created_at=current_time,
        expires_at=current_time
        + (request.expiration_time if request.expiration_time else 3600),
        message="浏览器会话创建任务已启动，正在后台处理",
    )

    return success_response(data=response_data)


@router.post(
    BrowserControlRouterPath.session_status,
    response_model=StandardResponse[BrowserSessionStatus],
)
async def browser_session_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    获取浏览器会话状态

    提供统一的会话状态查询，包含所有相关的状态信息。
    可以用来检查会话是否存在、浏览器是否运行、生命周期状态等。

    Returns:
        BrowserSessionStatus: 会话状态信息
    """
    status_data = LiveService.get_browser_session_status(auth_info.mid, browser_info.browser_id)

    # 确定状态码
    if not status_data.session_exists:
        code = 404  # 会话不存在
        msg = "浏览器会话不存在"
    elif not status_data.browser_running:
        code = 403  # 会话存在但浏览器未运行
        msg = "浏览器会话存在但未运行"
    else:
        code = 0  # 正常状态
        msg = "success"

    return (
        success_response(data=status_data)
        if code == 0
        else error_response(code=code, msg=msg)
    )
