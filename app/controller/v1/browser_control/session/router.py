from app.models.runtime.control import BrowserSessionStatusData
from app.services.RPA_browser.session.live_service import live_service
from fastapi import Depends, BackgroundTasks
import time
from app.config import settings
from app.models.runtime.control import (
    CreateSessionResponse,
    CloseSessionResponse,
    BrowserSessionStatus,
)
from bili_common.models.response import StandardResponse, success_response, error_response
from bili_common.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserSessionRouterPath
from app.services.RPA_browser.session.live_service import LiveService
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.security_depends import verify_browser_ownership
from bili_common.models.depends import BrowserReqInfo, BrowserReqAuthInfo
from ..base import new_session_router

router = new_session_router()


@router.post(
    BrowserSessionRouterPath.create,
    response_model=StandardResponse[CreateSessionResponse],
)
async def create_browser_session(
    background_tasks: BackgroundTasks,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
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

    if session_key in LiveService._browser_sessions:
        # 会话已存在，返回现有会话信息
        entry = LiveService._browser_sessions[session_key]
        created_at = getattr(entry, "created_at", entry.last_activity)
        expires_at = getattr(entry, "expires_at", None)
        # 获取当前会话状态，确保返回的是实际运行状态
        session_status = getattr(entry, "status", None)
        status_value = session_status.value if session_status else "running"

        response_data = CreateSessionResponse(
            success=True,
            session_id=session_key,
            browser_started=True,
            status=status_value,
            created_at=created_at,
            expires_at=expires_at,
            message="会话已存在，返回现有会话信息",
        )
        return success_response(data=response_data)
    await LiveService.create_browser_session(
        live_service,
        auth_info.mid,
        int(browser_info.browser_id),
    )

    # 立即返回响应，表示任务已启动
    current_time = int(time.time())
    expiration_time = settings.browser_session_expiration_time
    expires_at = (
        current_time + expiration_time
        if expiration_time
        else None
    )
    response_data = CreateSessionResponse(
        success=True,
        session_id=session_key,
        browser_started=True,
        status="running",
        created_at=current_time,
        expires_at=expires_at,
        message="浏览器会话已创建",
    )

    return success_response(data=response_data)


@router.post(
    BrowserSessionRouterPath.status,
    response_model=StandardResponse[BrowserSessionStatus],
)
async def browser_session_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    获取浏览器会话状态

    提供统一的会话状态查询，包含所有相关的状态信息。
    可以用来检查会话是否存在、浏览器是否运行、生命周期状态等。

    Returns:
        BrowserSessionStatus: 会话状态信息
    """
    status_data: BrowserSessionStatusData = live_service.get_browser_session_status(
        auth_info.mid,
        int(browser_info.browser_id)
    )

    # 会话状态查询是**只读语义**：「会话不存在」「浏览器未运行」都是正常状态，不是错误。
    # 状态完全由 data 的 session_exists / browser_running / lifecycle_state / status 表达，
    # 因此这里恒返回成功码，不再用错误码表达状态。
    #
    # 历史问题：此前返回 SESSION_NOT_FOUND(1006) / BROWSER_NOT_STARTED(1007)，
    # 而 add_error_status_middleware 会把业务码回写成 HTTP 状态
    # （http_status_for_code 对 1000+ 自定义码兜底为 400），于是「尚未建立会话」这个
    # 高频正常轮询结果变成了 HTTP 400，前端与日志都会按失败处理。
    return success_response(
        data=status_data,
        msg=status_data.message or "获取会话状态成功",
    )


@router.post(
    BrowserSessionRouterPath.close,
    response_model=StandardResponse[CloseSessionResponse],
)
async def close_browser_session(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    手动关闭浏览器会话

    主动关闭指定的浏览器会话，释放相关资源。
    如果会话不存在，将返回错误响应。

    Returns:
        CloseSessionResponse: 会话关闭结果
    """
    import time

    session_key = f"{auth_info.mid}_{browser_info.browser_id}"

    # 检查会话是否存在
    if session_key not in LiveService._browser_sessions:
        return error_response(
            code=ResponseCode.SESSION_NOT_FOUND,
            msg="浏览器会话不存在",
            data=CloseSessionResponse(
                success=False,
                session_id=session_key,
                browser_id=browser_info.browser_id,
                mid=auth_info.mid,
                closed_at=int(time.time()),
                message="浏览器会话不存在",
            ),
        )

    # 释放浏览器会话
    success = await live_service.release_browser_session(
        auth_info.mid, int(browser_info.browser_id)
    )

    current_time = int(time.time())

    if success:
        response_data = CloseSessionResponse(
            success=True,
            session_id=session_key,
            browser_id=browser_info.browser_id,
            mid=auth_info.mid,
            closed_at=current_time,
            message="浏览器会话已成功关闭",
        )
        return success_response(data=response_data)
    else:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR,
            msg="关闭浏览器会话失败",
            data=CloseSessionResponse(
                success=False,
                session_id=session_key,
                browser_id=browser_info.browser_id,
                mid=auth_info.mid,
                closed_at=current_time,
                message="关闭浏览器会话时发生错误",
            ),
        )
