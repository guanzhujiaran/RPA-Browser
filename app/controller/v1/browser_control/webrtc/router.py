from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.common.depends import BrowserReqInfo, BrowserReqAuthInfo
from ..base import new_webrtc_router
from pydantic import BaseModel

router = new_webrtc_router()

class WebRTCOfferRequest(BaseModel):
    page_index: int = 0

class WebRTCAnswerRequest(BaseModel):
    stream_key: str
    sdp: str
    type: str

class WebRTCIceCandidateRequest(BaseModel):
    stream_key: str
    candidate: str
    sdpMid: str
    sdpMLineIndex: int

class WebRTCCloseRequest(BaseModel):
    stream_key: str

@router.post(BrowserControlRouterPath.webrtc_offer, summary="创建 WebRTC Offer")
async def create_webrtc_offer(
    req: WebRTCOfferRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
):
    """
    创建 WebRTC Offer 以开始视频流传输。
    此接口会在现有会话上动态启用 WebRTC（如果尚未启用）。
    """
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        # 获取或启用 WebRTC 功能
        session = await LiveService.create_webrtc_enabled_session(mid, browser_id)
        
        # 获取 WebRTC 管理器并启动流
        webrtc_mgr = session.webrtc_manager
        logger.info(f"准备启动 WebRTC 流: mid={mid}, browser_id={browser_id}, page_index={req.page_index}")
        
        stream = await webrtc_mgr.start_stream(req.page_index)
        logger.info(f"WebRTC 流已启动: {stream.stream_key}, 当前活跃流: {list(webrtc_mgr.streams.keys())}")
        
        offer_data = await stream.create_offer()
        logger.info(f"Offer 创建成功: stream_key={offer_data['stream_key']}")
        
        return success_response(data=offer_data)
        
    except IndexError as e:
        logger.error(f"页面索引超出范围: {e}")
        return error_response(
            code=ResponseCode.PAGE_CLOSED,
            msg=str(e)
        )
    except Exception as e:
        logger.error(f"创建 WebRTC Offer 失败: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_OFFER_FAILED,
            msg=str(e)
        )

@router.post(BrowserControlRouterPath.webrtc_answer, summary="处理 WebRTC Answer")
async def handle_webrtc_answer(
    req: WebRTCAnswerRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
):
    """处理客户端返回的 SDP Answer"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        logger.info(f"处理 WebRTC Answer: stream_key={req.stream_key}")
        
        # 获取会话
        session_key = LiveService._get_session_key(mid, browser_id)
        if session_key not in LiveService.browser_sessions:
            logger.error(f"会话不存在: {session_key}")
            return error_response(
                code=ResponseCode.SESSION_NOT_FOUND,
                msg="会话不存在"
            )
        
        entry = LiveService.browser_sessions[session_key]
        logger.info(f"会话状态: has_webrtc={entry.has_webrtc()}")
        
        # 检查是否启用了 WebRTC
        if not entry.has_webrtc():
            logger.warning(
                f"会话 {session_key} 未启用 WebRTC。"
                f"请先调用 /webrtc/offer 接口创建流。"
            )
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg="WebRTC 未启用，请先调用 /webrtc/offer 创建流"
            )
        
        # 查找匹配的流（stream_key 包含 page_id）
        webrtc_mgr = entry.plugined_session.webrtc_manager
        logger.info(f"WebRTC 管理器中的活跃流: {[s.stream_key for s in webrtc_mgr.streams.values()]}")
        
        stream = None
        for s in webrtc_mgr.streams.values():
            if s.stream_key == req.stream_key:
                stream = s
                break
        
        if not stream:
            logger.warning(
                f"找不到 stream_key={req.stream_key} 的 WebRTC 流。"
                f"当前活跃流: {[s.stream_key for s in webrtc_mgr.streams.values()]}"
            )
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg=f"WebRTC 流 {req.stream_key} 不存在，请先调用 /webrtc/offer 创建"
            )
        
        logger.info(f"找到流: {stream.stream_key}, 状态: {stream.state.value}")
        
        # 处理 Answer
        await stream.handle_answer(req.sdp, req.type)
        logger.info(f"WebRTC Answer 处理成功: {stream.stream_key}")
        return success_response(msg="WebRTC Answer 已处理")
        
    except Exception as e:
        logger.error(f"处理 WebRTC Answer 失败: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_ANSWER_FAILED,
            msg=str(e)
        )

@router.post(BrowserControlRouterPath.webrtc_ice_candidate, summary="添加 ICE Candidate")
async def add_ice_candidate(
    req: WebRTCIceCandidateRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
):
    """添加 ICE Candidate"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        # 获取会话
        session_key = LiveService._get_session_key(mid, browser_id)
        if session_key not in LiveService.browser_sessions:
            return error_response(
                code=ResponseCode.SESSION_NOT_FOUND,
                msg="会话不存在"
            )
        
        entry = LiveService.browser_sessions[session_key]
        
        if not entry.has_webrtc():
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg="WebRTC 未启用，请先调用 /webrtc/offer 创建流"
            )
        
        # 查找匹配的流
        webrtc_mgr = entry.plugined_session.webrtc_manager
        stream = None
        for s in webrtc_mgr.streams.values():
            if s.stream_key == req.stream_key:
                stream = s
                break
        
        if not stream:
            logger.warning(
                f"找不到 stream_key={req.stream_key} 的 WebRTC 流。"
                f"当前活跃流: {[s.stream_key for s in webrtc_mgr.streams.values()]}"
            )
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg=f"WebRTC 流 {req.stream_key} 不存在"
            )
        
        await stream.add_ice_candidate(req.candidate, req.sdpMid, req.sdpMLineIndex)
        return success_response(msg="ICE Candidate 已添加")
        
    except Exception as e:
        logger.error(f"添加 ICE Candidate 失败: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_ICE_CANDIDATE_FAILED,
            msg=str(e)
        )

@router.post(BrowserControlRouterPath.webrtc_close, summary="关闭 WebRTC 流")
async def close_webrtc_stream(
    req: WebRTCCloseRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
):
    """关闭指定的 WebRTC 视频流"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        # 获取会话
        session_key = LiveService._get_session_key(mid, browser_id)
        if session_key not in LiveService.browser_sessions:
            return error_response(
                code=ResponseCode.SESSION_NOT_FOUND,
                msg="会话不存在"
            )
        
        entry = LiveService.browser_sessions[session_key]
        
        if not entry.has_webrtc():
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg="WebRTC 未启用，请先调用 /webrtc/offer 创建流"
            )
        
        webrtc_mgr = entry.plugined_session.webrtc_manager
        
        # 查找匹配的流（stream_key 包含 page_id）
        stream_to_close = None
        for page_id, stream in webrtc_mgr.streams.items():
            if stream.stream_key == req.stream_key:
                stream_to_close = stream
                break
        
        if not stream_to_close:
            logger.warning(
                f"找不到 stream_key={req.stream_key} 的 WebRTC 流。"
                f"当前活跃流: {[s.stream_key for s in webrtc_mgr.streams.values()]}"
            )
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg=f"WebRTC 流 {req.stream_key} 不存在"
            )
        
        # 关闭流
        await stream_to_close.close()
        
        # 从字典中移除
        page_id = stream_to_close.page_id
        if page_id in webrtc_mgr.streams:
            del webrtc_mgr.streams[page_id]
        
        logger.info(f"WebRTC 流已关闭: {req.stream_key}")
        return success_response(msg="WebRTC 流已关闭")
        
    except Exception as e:
        logger.error(f"关闭 WebRTC 流失败: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_CLOSE_FAILED,
            msg=str(e)
        )
