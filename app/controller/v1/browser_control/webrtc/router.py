from fastapi import Depends
from app.models.common.depends import VerifyBrowserDependsReq
from app.models.runtime.control import VideoStreamStatusResponse
from app.models.runtime.webrtc import (
    WebRTCOfferResponse,
    WebRTCAnswerRequest,
    WebRTCAnswerResponse,
    WebRTCIceCandidateRequest,
    WebRTCIceCandidateResponse,
    WebRTCConnectionStatusResponse,
    WebRTCCloseConnectionResponse,
)
from app.models.common.depends import BrowserReqAuthInfo
from app.models.exceptions.base_exception import BrowserNotStartedException
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.services.RPA_browser.webrtc_service import WebRTCService
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.depends.security_depends import verify_browser_ownership
from ..base import new_webrtc_router
import loguru

router = new_webrtc_router()


@router.post(
    BrowserControlRouterPath.stream_status,
    response_model=StandardResponse[VideoStreamStatusResponse],
)
async def get_video_stream_status(
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    检查浏览器视频流状态

    检查浏览器实例是否启动，如果启动则返回视频流URL。

    Returns:
        dict: 浏览器状态和视频流信息
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid
    # 检查浏览器会话状态
    session_status = LiveService.get_browser_session_status(mid, browser_id)

    if session_status.session_exists and session_status.browser_running:
        return success_response(
            data=VideoStreamStatusResponse(
                browser_id=browser_id,
                status="running",
                message="浏览器正在运行，可以获取视频流",
                active_connections=session_status.active_connections,
            )
        )


# WebRTC 视频流接口


@router.post(
    BrowserControlRouterPath.webrtc_offer,
    response_model=StandardResponse[WebRTCOfferResponse],
)
async def create_webrtc_offer(
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    创建 WebRTC offer

    为指定的浏览器实例创建 WebRTC offer，用于建立实时视频流连接。

    Args:
        request: 包含浏览器ID的请求

    Returns:
        WebRTCOfferResponse: 包含 SDP offer 的响应
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid


    # 检查浏览器会话状态
    session_status = LiveService.get_browser_session_status(mid, browser_id)
    if not session_status.session_exists or not session_status.browser_running:
        raise BrowserNotStartedException()

    # 创建 WebRTC offer
    offer = await WebRTCService.create_offer(mid, browser_id)

    loguru.logger.info(f"WebRTC offer created successfully for browser_id={browser_id}")

    return success_response(
        data=WebRTCOfferResponse(sdp=offer["sdp"], type=offer["type"])
    )


@router.post(
    BrowserControlRouterPath.webrtc_answer,
    response_model=StandardResponse[WebRTCAnswerResponse],
)
async def set_webrtc_answer(
    request: WebRTCAnswerRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    设置 WebRTC answer

    设置从客户端接收到的 WebRTC answer SDP。

    Args:
        request: 包含浏览器ID和 SDP answer 的请求

    Returns:
        WebRTCAnswerResponse: 操作结果
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid


    success = await WebRTCService.set_answer(mid, browser_id, request.sdp)

    if success:
        loguru.logger.info(
            f"WebRTC answer set successfully for browser_id={browser_id}"
        )
        return success_response(data=WebRTCAnswerResponse(success=success))
    else:
        loguru.logger.warning(
            f"WebRTC answer failed for browser_id={browser_id}: connection not found"
        )
        return error_response(
            code=ResponseCode.WEBRTC_ANSWER_FAILED,
            msg="Failed to set WebRTC answer: connection not found",
        )


@router.post(
    BrowserControlRouterPath.webrtc_ice_candidate,
    response_model=StandardResponse[WebRTCIceCandidateResponse],
)
async def add_webrtc_ice_candidate(
    request: WebRTCIceCandidateRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    添加 WebRTC ICE candidate

    添加从客户端接收到的 ICE candidate 数据。

    Args:
        request: 包含浏览器ID和 ICE candidate 数据的请求

    Returns:
        WebRTCIceCandidateResponse: 操作结果
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid

    # 🔧 调试日志：打印接收到的原始 candidate 数据
    loguru.logger.info(f"🔍 Received ICE candidate request for browser_id={browser_id}")
    loguru.logger.info(f"🔍 Candidate data: {request.candidate}")
    loguru.logger.info(
        f"🔍 Candidate keys: {list(request.candidate.keys()) if isinstance(request.candidate, dict) else 'Not a dict'}"
    )

    success = await WebRTCService.add_ice_candidate(mid, browser_id, request.candidate)

    if success:
        loguru.logger.info(f"WebRTC ICE candidate added for browser_id={browser_id}")
        return success_response(data=WebRTCIceCandidateResponse(success=success))
    else:
        # 这里的 false 只在解析失败时发生
        loguru.logger.error(
            f"WebRTC ICE candidate failed for browser_id={browser_id}: invalid format"
        )
        return error_response(
            code=ResponseCode.WEBRTC_ICE_CANDIDATE_FAILED,
            msg="Failed to add ICE candidate: invalid candidate format",
        )





@router.get(
    BrowserControlRouterPath.webrtc_status,
    response_model=StandardResponse[WebRTCConnectionStatusResponse],
)
async def get_webrtc_status(
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    获取 WebRTC 连接状态

    获取指定浏览器实例的 WebRTC 连接状态。

    Args:
        browser_id: 浏览器ID

    Returns:
        WebRTCConnectionStatusResponse: 连接状态信息
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid

    status = WebRTCService.get_connection_status(mid, browser_id)

    # 🔧 调试：显示缓存的 candidate 数量
    connection_key = f"{mid}_{browser_id}"
    cached_count = len(WebRTCService.ice_candidate_cache.get(connection_key, []))
    loguru.logger.info(
        f"🔍 Debug: Cached candidates for {connection_key}: {cached_count}"
    )

    return success_response(
        data=WebRTCConnectionStatusResponse(
            active=status["active"],
            ice_connection_state=status["ice_connection_state"],
            signaling_state=status["signaling_state"],
        )
    )


@router.post(
    BrowserControlRouterPath.webrtc_close,
    response_model=StandardResponse[WebRTCCloseConnectionResponse],
)
async def close_webrtc_connection(
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    关闭 WebRTC 连接

    关闭指定浏览器实例的 WebRTC 连接。

    Args:
        request: 包含浏览器ID的请求

    Returns:
        WebRTCCloseConnectionResponse: 操作结果
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid

    success = await WebRTCService.close_connection(mid, browser_id)

    if success:
        loguru.logger.info(
            f"WebRTC connection closed successfully for browser_id={browser_id}"
        )
        return success_response(data=WebRTCCloseConnectionResponse(success=success))
    else:
        loguru.logger.warning(
            f"WebRTC close failed for browser_id={browser_id}: connection not found"
        )
        return error_response(
            code=ResponseCode.WEBRTC_CLOSE_FAILED,
            msg="Failed to close WebRTC connection: connection not found",
        )
