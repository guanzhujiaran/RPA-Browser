"""
WebRTC 视频流 API 路由

所有端点均利用 WebRTCStreamManager 的双向索引实现 O(1) 流查找。
无需调用 enable_webrtc —— 管理器在会话创建时已自动就绪。
"""

from app.services.RPA_browser.session.live_service import live_service
from fastapi import Depends
from loguru import logger
from bili_common.models.response import success_response, error_response
from bili_common.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.session.live_service import LiveService
from app.utils.depends.security_depends import verify_browser_ownership_or_admin
from app.utils.depends.vip_depends import is_vip_user
from bili_common.models.depends import BrowserReqAuthInfo
from app.models.runtime.webrtc_models import (
    StreamQualityLevelEnum,
    StreamQualitySnapshot,
)
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


class WebRTCQualityRequest(BaseModel):
    """设置用户清晰度档位（见计划书 §5.18）"""

    level: StreamQualityLevelEnum


class WebRTCVisibilityRequest(BaseModel):
    """前端页面可见性信号（见计划书 §5.18）"""

    visible: bool


class WebRTCPauseRequest(BaseModel):
    """暂停 / 恢复视频发送（见计划书 §5.18）"""

    paused: bool


# ── 辅助：获取 WebRTC 管理器 ──

def _get_webrtc_manager(browser_req: BrowserReqAuthInfo):
    """从认证信息中获取 WebRTC 管理器"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    session_key = LiveService._get_session_key(mid, browser_id)

    if session_key not in LiveService._browser_sessions:
        return None, session_key

    entry = LiveService._browser_sessions[session_key]
    return entry.browser_session.webrtc_manager, session_key


# ── API 端点 ──


@router.post(BrowserControlRouterPath.webrtc_offer, summary="创建 WebRTC Offer")
async def create_webrtc_offer(
    req: WebRTCOfferRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """
    创建 WebRTC Offer 以开始视频流传输。
    会话不存在时自动创建，WebRTC 管理器已内建无需手动启用。
    """
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    is_vip = is_vip_user(browser_req.auth_info)

    # 🔑 内存不足时不阻塞在排队上，直接返回排队业务码，由前端轮询 /browser/session/status
    if live_service.would_queue_browser_session(mid, browser_id, is_vip):
        return error_response(
            code=ResponseCode.BROWSER_LAUNCH_MEMORY_INSUFFICIENT,
            msg="当前服务器内存不足，浏览器启动请求已进入排队，请稍后重试",
        )

    try:
        # 确保 WebRTC 就绪的会话存在（内存不足时按 VIP / 普通队列排队）
        entry = await live_service.ensure_webrtc_session(
            mid, browser_id, is_vip=is_vip
        )
        webrtc_mgr = entry.browser_session.webrtc_manager

        logger.info(
            f"准备启动 WebRTC 流: mid={mid}, browser_id={browser_id}, "
            f"page_index={req.page_index}"
        )

        stream = await webrtc_mgr.start_stream(req.page_index)
        logger.info(
            f"WebRTC 流已启动: {stream.stream_key}, "
            f"当前流: {list(webrtc_mgr.streams.keys())}"
        )

        offer_data = await stream.create_offer()
        # 顺带记录候选数：为 0 说明本端 ICE 收集异常，浏览器再怎么努力也连不上
        logger.info(
            f"Offer 创建成功: stream_key={offer_data['stream_key']}, "
            f"本端候选数={offer_data['sdp'].count('a=candidate')}"
        )

        return success_response(data=offer_data)

    except IndexError as e:
        logger.error(f"页面索引超出范围: {e}")
        return error_response(code=ResponseCode.PAGE_CLOSED, msg=str(e))
    except Exception as e:
        logger.error(f"创建 WebRTC Offer 失败: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_OFFER_FAILED, msg=str(e)
        )


@router.post(BrowserControlRouterPath.webrtc_answer, summary="处理 WebRTC Answer")
async def handle_webrtc_answer(
    req: WebRTCAnswerRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """处理客户端返回的 SDP Answer（O(1) 流查找）"""
    try:
        webrtc_mgr, session_key = _get_webrtc_manager(browser_req)
        if webrtc_mgr is None:
            return error_response(
                code=ResponseCode.SESSION_NOT_FOUND, msg="会话不存在"
            )

        logger.info(
            f"处理 WebRTC Answer: stream_key={req.stream_key}, "
            f"活跃流: {webrtc_mgr.get_stream_keys()}, "
            # 关键诊断：Chrome 通常要等 trickle 才给候选，这里为 0 就说明
            # 「候选只能靠 /webrtc/ice-candidate」——那条路一断，ICE 就永远停在 checking
            f"Answer 内候选数={req.sdp.count('a=candidate')}"
        )

        # O(1) stream_key 查找
        stream = webrtc_mgr.get_stream(stream_key=req.stream_key)
        if stream is None:
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg=f"WebRTC 流 {req.stream_key} 不存在，请先调用 /webrtc/offer",
            )

        logger.info(f"找到流: {stream.stream_key}, 状态: {stream.state.value}")
        await stream.handle_answer(req.sdp, req.type)
        await live_service.touch(
            browser_req.auth_info.mid, browser_req.browser_id, source="webrtc_answer"
        )
        logger.info(f"WebRTC Answer 处理成功: {stream.stream_key}")
        return success_response(msg="WebRTC Answer 已处理")

    except Exception as e:
        logger.error(f"处理 WebRTC Answer 失败: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_ANSWER_FAILED, msg=str(e)
        )


@router.post(
    BrowserControlRouterPath.webrtc_ice_candidate, summary="添加 ICE Candidate"
)
async def add_ice_candidate(
    req: WebRTCIceCandidateRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """添加 ICE Candidate（O(1) 流查找）"""
    try:
        webrtc_mgr, session_key = _get_webrtc_manager(browser_req)
        if webrtc_mgr is None:
            return error_response(
                code=ResponseCode.SESSION_NOT_FOUND, msg="会话不存在"
            )

        stream = webrtc_mgr.get_stream(stream_key=req.stream_key)
        if stream is None:
            # 这条分支以前是静默的：前端拿到 code!=0 也不 throw，结果「候选全被丢光」
            # 表现为 ICE 永远停在 checking、画面全黑，而后端一行日志都没有。必须留痕。
            logger.warning(
                f"丢弃 ICE Candidate：流不存在 stream_key={req.stream_key!r}，"
                f"当前流={list(webrtc_mgr.streams.keys())}"
            )
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg=f"WebRTC 流 {req.stream_key} 不存在",
            )

        await stream.add_ice_candidate(
            req.candidate, req.sdpMid, req.sdpMLineIndex
        )
        await live_service.touch(
            browser_req.auth_info.mid, browser_req.browser_id, source="webrtc_ice"
        )
        return success_response(msg="ICE Candidate 已添加")

    except Exception as e:
        logger.error(f"添加 ICE Candidate 失败: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_ICE_CANDIDATE_FAILED, msg=str(e)
        )


@router.post(BrowserControlRouterPath.webrtc_close, summary="关闭 WebRTC 流")
async def close_webrtc_stream(
    req: WebRTCCloseRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """关闭指定的 WebRTC 视频流（O(1) 查找 + 自动清理双索引）"""
    try:
        webrtc_mgr, session_key = _get_webrtc_manager(browser_req)
        if webrtc_mgr is None:
            return error_response(
                code=ResponseCode.SESSION_NOT_FOUND, msg="会话不存在"
            )

        await webrtc_mgr.close_stream(stream_key=req.stream_key)
        logger.info(f"WebRTC 流已关闭: {req.stream_key}")
        return success_response(msg="WebRTC 流已关闭")

    except Exception as e:
        logger.error(f"关闭 WebRTC 流失败: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_CLOSE_FAILED, msg=str(e)
        )


@router.post(
    BrowserControlRouterPath.webrtc_status, summary="获取 WebRTC 流状态"
)
async def get_webrtc_status(
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """获取当前浏览器会话的 WebRTC 流状态信息"""
    try:
        webrtc_mgr, session_key = _get_webrtc_manager(browser_req)
        if webrtc_mgr is None:
            # 只读状态查询：「尚未建立会话 / 流已关闭」是正常状态而非错误，
            # 返回空流列表（否则业务码 1006 会被错误状态中间件回写成 HTTP 400）
            return success_response(
                data={"enabled": False, "active_streams": [], "total_streams": 0},
                msg="会话不存在或暂无 WebRTC 流",
            )

        streams_info = []
        for page_index, stream in webrtc_mgr.streams.items():
            streams_info.append(
                {
                    "stream_key": stream.stream_key,
                    "page_index": page_index,
                    "state": stream.state.value,
                    "idle_duration": round(stream.idle_duration, 1),
                }
            )

        return success_response(
            data={
                "enabled": True,
                "active_streams": streams_info,
                "total_streams": len(streams_info),
            }
        )

    except Exception as e:
        logger.error(f"获取 WebRTC 状态失败: {e}")
        return error_response(
            code=ResponseCode.WEBRTC_STATUS_FAILED, msg=str(e)
        )


@router.post(
    BrowserControlRouterPath.webrtc_quality, summary="设置 WebRTC 清晰度档位"
)
async def set_webrtc_quality(
    req: WebRTCQualityRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """设置当前会话的清晰度档位（高清 / 标清 / 流畅）

    档位切换通过重启 screencast 生效，**不重建 WebRTC 连接**，前端无需重新 offer/answer。

    实际生效档位由「用户档位」「页面可见性」「会话闲置」三者取最省决定（见计划书 §5.18），
    因此响应里的 effective_level 可能低于请求的 level —— 这是预期行为，不是失败。
    流未运行时档位记在会话上，下次开流生效。
    """
    try:
        webrtc_mgr, _ = _get_webrtc_manager(browser_req)
        if webrtc_mgr is None:
            return error_response(
                code=ResponseCode.BROWSER_NOT_STARTED,
                msg="浏览器未启动或已停止",
            )

        await webrtc_mgr.set_level(req.level)
        return success_response(data=webrtc_mgr.quality_snapshot())

    except Exception as e:
        logger.error(f"设置 WebRTC 清晰度档位失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_STATUS_FAILED, msg=str(e))


@router.post(
    BrowserControlRouterPath.webrtc_visibility,
    summary="上报页面可见性（自动降载）",
)
async def report_webrtc_visibility(
    req: WebRTCVisibilityRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """上报页面可见性，供前端切后台 / 组件被遮挡时自动降档

    `visible=false` 降到最省档、`true` 回落到用户档位（见计划书 §5.18）。
    与后端闲置降档独立并存、取最省，互不覆盖。
    """
    try:
        webrtc_mgr, _ = _get_webrtc_manager(browser_req)
        if webrtc_mgr is None:
            # 会话已回收时可见性信号已无意义：静默成功，避免前端把「切后台」
            # 误报成「浏览器未启动」而弹出重新启动的引导
            return success_response(
                data=None, msg="会话不存在或暂无 WebRTC 流，可见性信号已忽略"
            )

        await webrtc_mgr.set_visibility(req.visible)
        return success_response(data=webrtc_mgr.quality_snapshot())

    except Exception as e:
        logger.error(f"上报 WebRTC 可见性失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_STATUS_FAILED, msg=str(e))


@router.post(
    BrowserControlRouterPath.webrtc_pause, summary="暂停 / 恢复 WebRTC 视频发送"
)
async def set_webrtc_paused(
    req: WebRTCPauseRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """暂停 / 恢复视频发送（见计划书 §5.18）

    暂停时后端停止 screencast（浏览器侧不再做 JPEG 编码），带宽与 CPU 归零；
    接收端画面停留在最后一帧。**不重建 WebRTC 连接**，恢复立即生效、无需重新协商。
    """
    try:
        webrtc_mgr, _ = _get_webrtc_manager(browser_req)
        if webrtc_mgr is None:
            return error_response(
                code=ResponseCode.BROWSER_NOT_STARTED,
                msg="浏览器未启动或已停止",
            )

        await webrtc_mgr.set_paused(req.paused)
        return success_response(data=webrtc_mgr.quality_snapshot())

    except Exception as e:
        logger.error(f"设置 WebRTC 暂停态失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_STATUS_FAILED, msg=str(e))
