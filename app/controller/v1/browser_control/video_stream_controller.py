from fastapi import Depends
from app.models.RPA_browser.depends_models import VerifyBrowserDependsReq
from app.models.RPA_browser.live_control_models import VideoStreamStatusResponse
from app.models.RPA_browser.webrtc_models import (
    WebRTCOfferRequest,
    WebRTCOfferResponse,
    WebRTCAnswerRequest,
    WebRTCAnswerResponse,
    WebRTCIceCandidateRequest,
    WebRTCIceCandidateResponse,
    WebRTCGetIceCandidatesResponse,
    WebRTCConnectionStatusResponse,
    WebRTCCloseConnectionRequest,
    WebRTCCloseConnectionResponse,
)
from app.models.exceptions.base_exception import BrowserNotStartedException
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.services.RPA_browser.webrtc_service import WebRTCService
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.stream_base import new_router
import loguru
from sqlmodel.ext.asyncio.session import AsyncSession

router = new_router()


@router.post(
    BrowserControlRouterPath.stream_status,
    response_model=StandardResponse[VideoStreamStatusResponse],
)
async def get_video_stream_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    检查浏览器视频流状态

    检查浏览器实例是否启动，如果启动则返回视频流URL。

    Returns:
        dict: 浏览器状态和视频流信息
    """
    from app.config import settings

    # 检查浏览器会话状态
    session_status = LiveService.get_browser_session_status(
 auth_info.mid, browser_info.browser_id
    )

    if session_status.session_exists and session_status.browser_running:
        return success_response(
            data=VideoStreamStatusResponse(
                browser_id=browser_info.browser_id,
                status="running",
                stream_url=f"{settings.controller_base_path}{router.prefix}{BrowserControlRouterPath.stream_mjpeg}?browser_id={browser_info.browser_id}",
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
    request: WebRTCOfferRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    创建 WebRTC offer

    为指定的浏览器实例创建 WebRTC offer，用于建立实时视频流连接。

    Args:
        request: 包含浏览器ID的请求

    Returns:
        WebRTCOfferResponse: 包含 SDP offer 的响应
    """
    browser_id = int(request.browser_id_str)

    await verify_browser_ownership(
        body=VerifyBrowserDependsReq(browser_id=browser_id),
        mid=mid,
        session=session,
    )

    # 检查浏览器会话状态
    session_status = LiveService.get_browser_session_status(auth_info.mid, browser_id)
    if not session_status.session_exists or not session_status.browser_running:
        raise BrowserNotStartedException()

    try:
        # 创建 WebRTC offer
        offer = await WebRTCService.create_offer(auth_info.mid, browser_id)

        loguru.logger.info(f"WebRTC offer created successfully for browser_id={browser_id}")

        return success_response(
            data=WebRTCOfferResponse(
                sdp=offer["sdp"],
                type=offer["type"]
            )
        )

    except Exception as e:
        loguru.logger.error(f"WebRTC offer creation failed for browser_id={browser_id}: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_OFFER_FAILED,
            msg=f"WebRTC offer creation failed: {str(e)}"
        )


@router.post(
    BrowserControlRouterPath.webrtc_answer,
    response_model=StandardResponse[WebRTCAnswerResponse],
)
async def set_webrtc_answer(
    request: WebRTCAnswerRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    设置 WebRTC answer

    设置从客户端接收到的 WebRTC answer SDP。

    Args:
        request: 包含浏览器ID和 SDP answer 的请求

    Returns:
        WebRTCAnswerResponse: 操作结果
    """
    browser_id = int(request.browser_id_str)

    await verify_browser_ownership(
        body=VerifyBrowserDependsReq(browser_id=browser_id),
        mid=mid,
        session=session,
    )

    try:
        success = await WebRTCService.set_answer(auth_info.mid, browser_id, request.sdp)

        if success:
            loguru.logger.info(f"WebRTC answer set successfully for browser_id={browser_id}")
            return success_response(
                data=WebRTCAnswerResponse(success=success)
            )
        else:
            loguru.logger.warning(f"WebRTC answer failed for browser_id={browser_id}: connection not found")
            return error_response(
                code=ResponseCode.WEBRTC_ANSWER_FAILED,
                msg="Failed to set WebRTC answer: connection not found"
            )

    except Exception as e:
        loguru.logger.error(f"WebRTC answer setting failed: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_ANSWER_FAILED,
            msg=f"WebRTC answer setting failed: {str(e)}"
        )


@router.post(
    BrowserControlRouterPath.webrtc_ice_candidate,
    response_model=StandardResponse[WebRTCIceCandidateResponse],
)
async def add_webrtc_ice_candidate(
    request: WebRTCIceCandidateRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    添加 WebRTC ICE candidate

    添加从客户端接收到的 ICE candidate 数据。

    Args:
        request: 包含浏览器ID和 ICE candidate 数据的请求

    Returns:
        WebRTCIceCandidateResponse: 操作结果
    """
    browser_id = int(request.browser_id_str)

    await verify_browser_ownership(
        body=VerifyBrowserDependsReq(browser_id=browser_id),
        mid=mid,
        session=session,
    )

    # 🔧 调试日志：打印接收到的原始 candidate 数据
    loguru.logger.info(f"🔍 Received ICE candidate request for browser_id={browser_id}")
    loguru.logger.info(f"🔍 Candidate data: {request.candidate}")
    loguru.logger.info(f"🔍 Candidate keys: {list(request.candidate.keys()) if isinstance(request.candidate, dict) else 'Not a dict'}")

    try:
        success = await WebRTCService.add_ice_candidate(auth_info.mid, browser_id, request.candidate)

        if success:
            loguru.logger.info(f"WebRTC ICE candidate added for browser_id={browser_id}")
            return success_response(
                data=WebRTCIceCandidateResponse(success=success)
            )
        else:
            # 这里的 false 只在解析失败时发生
            loguru.logger.error(f"WebRTC ICE candidate failed for browser_id={browser_id}: invalid format")
            return error_response(
                code=ResponseCode.WEBRTC_ICE_CANDIDATE_FAILED,
                msg="Failed to add ICE candidate: invalid candidate format"
            )

    except Exception as e:
        loguru.logger.error(f"WebRTC ICE candidate addition failed for browser_id={browser_id}: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_ICE_CANDIDATE_FAILED,
            msg=f"WebRTC ICE candidate addition failed: {str(e)}"
        )


@router.get(
    BrowserControlRouterPath.webrtc_ice_candidates_get,
    response_model=StandardResponse[WebRTCGetIceCandidatesResponse],
)
async def get_webrtc_ice_candidates(
    browser_id: int | str,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    获取服务端的 ICE candidates

    获取后端生成的 ICE candidates，用于建立 WebRTC 连接。

    Args:
        browser_id: 浏览器ID

    Returns:
        WebRTCGetIceCandidatesResponse: ICE candidates 列表和 ICE gathering 状态
    """
    browser_id = int(browser_id)
    await verify_browser_ownership(
        body=VerifyBrowserDependsReq(browser_id=browser_id),
        mid=mid,
        session=session,
    )

    try:
        candidates, ice_gathering_state = WebRTCService.get_server_ice_candidates(auth_info.mid, browser_id)
        return success_response(
            data=WebRTCGetIceCandidatesResponse(
                candidates=candidates,
                ice_gathering_state=ice_gathering_state
            )
        )
    except Exception as e:
        loguru.logger.error(f"Failed to get ICE candidates for browser_id={browser_id}: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_STATUS_FAILED,
            msg=f"Failed to get ICE candidates: {str(e)}"
        )


@router.get(
    BrowserControlRouterPath.webrtc_status,
    response_model=StandardResponse[WebRTCConnectionStatusResponse],
)
async def get_webrtc_status(
    browser_id: int | str,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    获取 WebRTC 连接状态

    获取指定浏览器实例的 WebRTC 连接状态。

    Args:
        browser_id: 浏览器ID

    Returns:
        WebRTCConnectionStatusResponse: 连接状态信息
    """
    browser_id = int(browser_id)
    await verify_browser_ownership(
        body=VerifyBrowserDependsReq(browser_id=browser_id),
        mid=mid,
        session=session,
    )

    status = WebRTCService.get_connection_status(auth_info.mid, browser_id)

    # 🔧 调试：显示缓存的 candidate 数量
    connection_key = f"{auth_info.mid}_{browser_id}"
    cached_count = len(WebRTCService.ice_candidate_cache.get(connection_key, []))
    loguru.logger.info(f"🔍 Debug: Cached candidates for {connection_key}: {cached_count}")

    return success_response(
        data=WebRTCConnectionStatusResponse(
            active=status["active"],
            ice_connection_state=status["ice_connection_state"],
            signaling_state=status["signaling_state"]
        )
    )


@router.post(
    BrowserControlRouterPath.webrtc_close,
    response_model=StandardResponse[WebRTCCloseConnectionResponse],
)
async def close_webrtc_connection(
    request: WebRTCCloseConnectionRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    关闭 WebRTC 连接

    关闭指定浏览器实例的 WebRTC 连接。

    Args:
        request: 包含浏览器ID的请求

    Returns:
        WebRTCCloseConnectionResponse: 操作结果
    """
    browser_id = int(request.browser_id_str)

    await verify_browser_ownership(
        body=VerifyBrowserDependsReq(browser_id=browser_id),
        mid=mid,
        session=session,
    )

    try:
        success = await WebRTCService.close_connection(auth_info.mid, browser_id)

        if success:
            loguru.logger.info(f"WebRTC connection closed successfully for browser_id={browser_id}")
            return success_response(
                data=WebRTCCloseConnectionResponse(success=success)
            )
        else:
            loguru.logger.warning(f"WebRTC close failed for browser_id={browser_id}: connection not found")
            return error_response(
                code=ResponseCode.WEBRTC_CLOSE_FAILED,
                msg="Failed to close WebRTC connection: connection not found"
            )

    except Exception as e:
        loguru.logger.error(f"WebRTC connection closing failed: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_CLOSE_FAILED,
            msg=f"WebRTC connection closing failed: {str(e)}"
        )
