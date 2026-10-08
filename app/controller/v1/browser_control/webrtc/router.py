"""
WebRTC 视频流 API 路由（多观看者并发直播）

一个观看者 = 一条 PeerConnection，由前端生成的 `viewer_id` 标识
（见 docs/rpa-多观看者并发直播计划书.md）：
- `stream_key` 尾部带 `viewer_id`，信令端点按 **viewer_id + stream_key 双重校验**定位，
  避免「延迟到达的旧信令命中别人的流」（改造前 `stream_key` 不含观看者标识，属串流污染）。
- 档位 / 暂停 / 可见性都是**观看者级**；有独立的保活心跳端点。
- 观看者资源初始为空壳，首次 /webrtc/offer 时才分配（无需 enable_webrtc）。
"""

from app.services.RPA_browser.session.live_service import live_service
from fastapi import Depends
from loguru import logger
from bili_common.models.response import success_response, error_response
from bili_common.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.session.live_service import LiveService
from app.services.RPA_browser.webrtc.stream_manager import WebRTCStreamManager
from app.services.RPA_browser.webrtc.viewer_stream import ViewerStream
from app.utils.depends.client_context import ClientContext, get_client_context
from app.utils.depends.security_depends import verify_browser_ownership_or_admin
from app.utils.depends.vip_depends import is_vip_user
from bili_common.models.depends import BrowserReqAuthInfo
from app.models.runtime.webrtc_models import (
    StreamQualityLevelEnum,
    StreamQualitySnapshot,
)
from app.config import settings
from ..base import new_webrtc_router
from pydantic import BaseModel, Field

router = new_webrtc_router()


# ── 请求模型 ──
# viewer_id 由前端生成（每次建立连接一枚，见前端需求文档 §3.1）


class WebRTCOfferRequest(BaseModel):
    page_index: int = 0
    viewer_id: str = Field(min_length=1, max_length=64, description="观看者标识")


class WebRTCAnswerRequest(BaseModel):
    viewer_id: str = Field(min_length=1, max_length=64, description="观看者标识")
    stream_key: str
    sdp: str
    type: str


class WebRTCIceCandidateRequest(BaseModel):
    viewer_id: str = Field(min_length=1, max_length=64, description="观看者标识")
    stream_key: str
    candidate: str
    sdpMid: str
    sdpMLineIndex: int


class WebRTCIceCandidateItem(BaseModel):
    """单个 ICE 候选（批量端点用）"""

    candidate: str
    sdpMid: str = ""
    sdpMLineIndex: int = 0


class WebRTCIceCandidatesRequest(BaseModel):
    """批量 ICE 候选（见计划书 §10.9：建连期攒批上报，替代逐个 POST）"""

    viewer_id: str = Field(min_length=1, max_length=64, description="观看者标识")
    stream_key: str
    candidates: list[WebRTCIceCandidateItem] = Field(min_length=1, max_length=32)


class WebRTCCloseRequest(BaseModel):
    viewer_id: str = Field(min_length=1, max_length=64, description="观看者标识")
    stream_key: str = ""


class WebRTCQualityRequest(BaseModel):
    """设置本观看者的清晰度档位（见计划书 §5.18）"""

    viewer_id: str = Field(min_length=1, max_length=64, description="观看者标识")
    level: StreamQualityLevelEnum


class WebRTCVisibilityRequest(BaseModel):
    """本观看者的页面可见性信号（见计划书 §5.18）"""

    viewer_id: str = Field(min_length=1, max_length=64, description="观看者标识")
    visible: bool


class WebRTCPauseRequest(BaseModel):
    """本观看者的暂停 / 恢复（见计划书 §5.18）"""

    viewer_id: str = Field(min_length=1, max_length=64, description="观看者标识")
    paused: bool


class WebRTCHeartbeatRequest(BaseModel):
    """观看者保活（多观看者并发直播）"""

    viewer_id: str = Field(min_length=1, max_length=64, description="观看者标识")


# ── 辅助：获取 WebRTC 管理器 ──


def _resolve_session(
    browser_req: BrowserReqAuthInfo,
) -> tuple[WebRTCStreamManager | None, str, int, bool]:
    """定位目标会话（含监管管理员访问**他人**浏览器的场景）。

    - 普通用户 / 浏览器属于自己：会话键 = `{自己的 mid}_{browser_id}`；
    - 监管管理员访问他人浏览器：请求携带的是**管理员自己的 mid**，拼不出归属者的键，
      因此回退为按 browser_id 反查会话条目
      （见 docs/rpa-多观看者并发直播计划书.md §10.5）。

    ⚠️ 调用方必须已依赖 `verify_browser_ownership_or_admin` 完成鉴权，
    否则这里会成为越权读取他人会话的后门。

    Returns:
        (WebRTC 管理器, session_key, 归属者 mid, 是否监管访问)；
        会话不存在时管理器为 None
    """
    mid = int(browser_req.auth_info.mid)
    browser_id = browser_req.browser_id

    own_key = LiveService._get_session_key(mid, browser_id)
    entry = LiveService._browser_sessions.get(own_key)
    if entry is not None:
        return entry.browser_session.webrtc_manager, own_key, mid, False

    # 监管场景：浏览器属于他人 → 按 browser_id 反查归属者会话
    owner_entry = live_service.find_session_entry_by_browser_id(browser_id)
    if owner_entry is None:
        return None, own_key, mid, False

    owner_key = LiveService._get_session_key(owner_entry.mid, browser_id)
    logger.info(
        f"👨‍💼 监管观看: admin_mid={mid} → owner_mid={owner_entry.mid}, "
        f"browser_id={browser_id}"
    )
    return (
        owner_entry.browser_session.webrtc_manager,
        owner_key,
        int(owner_entry.mid),
        True,
    )


def _resolve_viewer(
    webrtc_mgr: WebRTCStreamManager,
    viewer_id: str,
    stream_key: str | None = None,
) -> tuple[ViewerStream | None, str]:
    """定位观看者并校验归属。

    `stream_key` 尾部含 `viewer_id`，两者必须一致 —— 只按 stream_key 查找会让
    「已被回收 / 他人观看者」的延迟信令命中别的流（改造前的串流污染，见计划书 §2.5）。

    Returns:
        (观看者, 错误信息)；观看者存在且校验通过时错误信息为空串
    """
    viewer = webrtc_mgr.get_viewer(viewer_id)
    if viewer is None:
        return None, f"观看者 {viewer_id} 不存在或已回收，请重新调用 /webrtc/offer"

    if stream_key and viewer.stream_key != stream_key:
        return None, (
            "stream_key 与观看者不匹配（延迟信令或串流污染）: "
            f"viewer={viewer_id}, 请求={stream_key}, 实际={viewer.stream_key}"
        )
    return viewer, ""


# ── API 端点 ──


@router.post(BrowserControlRouterPath.webrtc_offer, summary="创建 WebRTC Offer")
async def create_webrtc_offer(
    req: WebRTCOfferRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
    client: ClientContext = Depends(get_client_context),
):
    """
    为指定观看者创建 WebRTC Offer 以开始视频流传输。

    **同一会话可并发多个观看者**（各自一条连接），互不淘汰。
    同一 `viewer_id` 再次调用视为重连：会先关闭自己的旧连接再新建。
    会话不存在时自动创建。
    """
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    is_vip = is_vip_user(browser_req.auth_info)

    try:
        webrtc_mgr, session_key, owner_mid, is_admin_view = _resolve_session(
            browser_req
        )

        if is_admin_view:
            # 监管观看：目标浏览器必须**已在运行**。
            # 绝不能走 ensure_webrtc_session —— 那会用管理员的 mid 创建出
            # 第二个浏览器实例（而不是观看目标会话）。
            if webrtc_mgr is None:
                return error_response(
                    code=ResponseCode.BROWSER_NOT_STARTED,
                    msg="目标浏览器未在运行，无法观看",
                )
        else:
            # 🔑 内存不足时不阻塞在排队上，直接返回排队业务码，由前端 SSE / 状态接口感知
            if live_service.would_queue_browser_session(mid, browser_id, is_vip):
                return error_response(
                    code=ResponseCode.BROWSER_LAUNCH_MEMORY_INSUFFICIENT,
                    msg="当前服务器内存不足，浏览器启动请求已进入排队，请稍后重试",
                )
            # 确保 WebRTC 就绪的会话存在（内存不足时按 VIP / 普通队列排队）
            entry = await live_service.ensure_webrtc_session(
                mid, browser_id, is_vip=is_vip
            )
            webrtc_mgr = entry.browser_session.webrtc_manager

        # 人数上限（默认 0 = 不限制）：同一 viewer 的重连不计入新增
        max_viewers = settings.browser_webrtc_max_viewers
        if (
            max_viewers > 0
            and webrtc_mgr.get_viewer(req.viewer_id) is None
            and webrtc_mgr.viewer_count >= max_viewers
        ):
            logger.warning(
                f"观看人数已达上限，拒绝新观看者: viewer={req.viewer_id}, "
                f"当前={webrtc_mgr.viewer_count}, 上限={max_viewers}"
            )
            return error_response(
                code=ResponseCode.WEBRTC_OFFER_FAILED,
                msg=f"同时观看人数已达上限（{max_viewers}），请稍后再试",
            )

        logger.debug(
            f"准备启动 WebRTC 流: mid={mid}, browser_id={browser_id}, "
            f"page_index={req.page_index}, viewer={req.viewer_id}, "
            f"当前观看者数={webrtc_mgr.viewer_count}"
        )

        # 监管观看对浏览器归属者**完全隐藏**（不计入对外人数、不出现在观看者列表）
        # —— 见计划书 §2.7。
        viewer = await webrtc_mgr.start_stream(
            req.page_index,
            req.viewer_id,
            client_ip=client.ip,
            client_device=client.device,
            client_device_type=client.device_type,
            client_browser_version=client.browser_version,
            client_ip_region=client.ip_region,
            client_ip_isp=client.ip_isp,
            is_admin=is_admin_view,
        )
        logger.info(
            f"WebRTC 观看者接入: {viewer.stream_key} | {viewer.client_summary} "
            f"| 当前观看者={webrtc_mgr.viewer_count}"
        )

        offer_data = await viewer.create_offer()
        # 顺带记录候选数：为 0 说明本端 ICE 收集异常，浏览器再怎么努力也连不上
        logger.debug(
            f"Offer 创建成功: stream_key={offer_data['stream_key']}, "
            f"本端候选数={offer_data['sdp'].count('a=candidate')}"
        )

        # 观看者加入：立即推送新快照（viewer_count / viewers 已变化）。
        # 不依赖 answer 的 touch 兜底 —— 用户拿到 offer 后可能取消，
        # 那样人数会一直停在旧值（心跳下线后没有其他触发点，见计划书 §10.7）。
        live_service.notify_session_status(owner_mid, browser_req.browser_id)

        return success_response(data=offer_data)

    except IndexError as e:
        logger.error(f"页面索引超出范围: {e}")
        return error_response(code=ResponseCode.PAGE_CLOSED, msg=str(e))
    except Exception as e:
        logger.error(f"创建 WebRTC Offer 失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_OFFER_FAILED, msg=str(e))


@router.post(BrowserControlRouterPath.webrtc_answer, summary="处理 WebRTC Answer")
async def handle_webrtc_answer(
    req: WebRTCAnswerRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """处理客户端返回的 SDP Answer（O(1) 查找 + 归属校验）"""
    try:
        webrtc_mgr, session_key, owner_mid, is_admin_view = _resolve_session(
            browser_req
        )
        if webrtc_mgr is None:
            return error_response(code=ResponseCode.SESSION_NOT_FOUND, msg="会话不存在")

        viewer, err = _resolve_viewer(webrtc_mgr, req.viewer_id, req.stream_key)
        if viewer is None:
            logger.warning(f"拒绝 WebRTC Answer: {err}")
            return error_response(code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE, msg=err)

        logger.info(
            f"处理 WebRTC Answer: stream_key={req.stream_key}, "
            f"活跃观看者: {[v.stream_key for v in webrtc_mgr.viewers_info()]}, "
            # 关键诊断：Chrome 通常要等 trickle 才给候选，这里为 0 就说明
            # 「候选只能靠 /webrtc/ice-candidate」——那条路一断，ICE 就永远停在 checking
            f"Answer 内候选数={req.sdp.count('a=candidate')}"
        )

        await viewer.handle_answer(req.sdp, req.type)
        # 用**归属者** mid 续期：监管观看时管理员的 mid 对应不到任何会话
        await live_service.touch(
            owner_mid, browser_req.browser_id, source="webrtc_answer"
        )
        logger.debug(f"WebRTC Answer 处理成功: {viewer.stream_key}")
        return success_response(msg="WebRTC Answer 已处理")

    except Exception as e:
        logger.error(f"处理 WebRTC Answer 失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_ANSWER_FAILED, msg=str(e))


@router.post(
    BrowserControlRouterPath.webrtc_ice_candidate, summary="添加 ICE Candidate"
)
async def add_ice_candidate(
    req: WebRTCIceCandidateRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """添加 ICE Candidate（O(1) 查找 + 归属校验）"""
    try:
        webrtc_mgr, session_key, owner_mid, is_admin_view = _resolve_session(
            browser_req
        )
        if webrtc_mgr is None:
            return error_response(code=ResponseCode.SESSION_NOT_FOUND, msg="会话不存在")

        viewer, err = _resolve_viewer(webrtc_mgr, req.viewer_id, req.stream_key)
        if viewer is None:
            # 这条分支以前是静默的：前端拿到 code!=0 也不 throw，结果「候选全被丢光」，
            # 表现为 ICE 永远停在 checking、画面全黑，而后端一行日志都没有。必须留痕。
            logger.warning(f"丢弃 ICE Candidate: {err}")
            return error_response(code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE, msg=err)

        added = await viewer.add_ice_candidate(
            req.candidate, req.sdpMid, req.sdpMLineIndex
        )
        # 同上：续期目标始终是归属者的会话
        await live_service.touch(owner_mid, browser_req.browser_id, source="webrtc_ice")
        if not added:
            # 跳过的只有「解析不出来的 mDNS host 候选」——跨网段 / 容器部署下必然发生，
            # 属可预期情况，不是失败：返回成功码，避免前端按失败处理（它会重试并报错）。
            # 服务端记 DEBUG + 计数留痕（见 ViewerStream.add_ice_candidate），
            # 真连不上时由 ICE failed 的 ERROR 汇总「跳过了多少个 mDNS 候选」。
            return success_response(msg="已跳过无法解析的 mDNS 候选（不影响连接）")
        return success_response(msg="ICE Candidate 已添加")

    except Exception as e:
        logger.error(f"添加 ICE Candidate 失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_ICE_CANDIDATE_FAILED, msg=str(e))


@router.post(
    BrowserControlRouterPath.webrtc_ice_candidates,
    summary="批量添加 ICE Candidate",
)
async def add_ice_candidates(
    req: WebRTCIceCandidatesRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """批量添加 ICE Candidate（计划书 §10.9）。

    建连期 Chrome 会陆续产出 3~6 个候选，逐个 POST 就是同量级的额外请求；
    前端攒 200ms 批量上报，建连期请求量降一个量级。
    单个候选失败（如无法解析的 mDNS host 候选）不影响批次内其他候选，
    与单候选端点语义一致：被跳过的候选已由 ViewerStream 记 WARNING 留痕。

    Returns:
        data: {total, added, skipped}
    """
    try:
        webrtc_mgr, _, owner_mid, _ = _resolve_session(browser_req)
        if webrtc_mgr is None:
            return error_response(code=ResponseCode.SESSION_NOT_FOUND, msg="会话不存在")

        viewer, err = _resolve_viewer(webrtc_mgr, req.viewer_id, req.stream_key)
        if viewer is None:
            logger.warning(f"丢弃批量 ICE Candidate: {err}")
            return error_response(code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE, msg=err)

        added = 0
        skipped = 0
        for item in req.candidates:
            if await viewer.add_ice_candidate(
                item.candidate, item.sdpMid, item.sdpMLineIndex
            ):
                added += 1
            else:
                skipped += 1

        # 用归属者 mid 续期（监管场景管理员 mid 对应不到任何会话）
        await live_service.touch(owner_mid, browser_req.browser_id, source="webrtc_ice")
        logger.debug(
            f"批量 ICE Candidate 已处理: stream_key={viewer.stream_key}, "
            f"共 {len(req.candidates)} 个（加入 {added} / 跳过 {skipped}）"
        )
        return success_response(
            data={"total": len(req.candidates), "added": added, "skipped": skipped},
            msg="ICE Candidate 已批量添加",
        )

    except Exception as e:
        logger.error(f"批量添加 ICE Candidate 失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_ICE_CANDIDATE_FAILED, msg=str(e))


@router.post(BrowserControlRouterPath.webrtc_close, summary="关闭 WebRTC 流")
async def close_webrtc_stream(
    req: WebRTCCloseRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """关闭**本观看者**的 WebRTC 视频流（不影响其他观看者）"""
    try:
        webrtc_mgr, session_key, owner_mid, is_admin_view = _resolve_session(
            browser_req
        )
        if webrtc_mgr is None:
            return error_response(code=ResponseCode.SESSION_NOT_FOUND, msg="会话不存在")

        # 先取客户端摘要：close_viewer 会把观看者从管理器里移除，之后再也查不到
        viewer = webrtc_mgr.get_viewer(req.viewer_id)

        # 幂等：观看者已不存在时视为已关闭（例如链路失联被后台回收）
        closed = await webrtc_mgr.close_viewer(req.viewer_id)
        if not closed:
            logger.info(f"关闭观看者流：观看者已不存在（视为已关闭）: {req.viewer_id}")
        else:
            logger.info(
                f"WebRTC 观看者断开: {req.viewer_id} | {viewer.client_summary} "
                f"| 观看时长={viewer.watch_seconds}s "
                f"| 剩余观看者={webrtc_mgr.viewer_count}"
            )
            # 观看者离开：立即推送（人数 -1 / 列表移除）。
            # 心跳下线后没有其他触发点，不推则前端人数停在旧值。
            live_service.notify_session_status(owner_mid, browser_req.browser_id)
        return success_response(msg="WebRTC 流已关闭")

    except Exception as e:
        logger.error(f"关闭 WebRTC 流失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_CLOSE_FAILED, msg=str(e))


@router.post(BrowserControlRouterPath.webrtc_status, summary="获取 WebRTC 流状态")
async def get_webrtc_status(
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """获取当前浏览器会话的 WebRTC 流状态信息（只读，**不保活**）"""
    try:
        webrtc_mgr, session_key, owner_mid, is_admin_view = _resolve_session(
            browser_req
        )
        if webrtc_mgr is None:
            # 只读状态查询：「尚未建立会话 / 流已关闭」是正常状态而非错误，
            # 返回空列表（否则业务码 1006 会被错误状态中间件回写成 HTTP 400）
            return success_response(
                data={
                    "enabled": False,
                    "active_streams": [],
                    "total_streams": 0,
                    "viewer_count": 0,
                    "viewers": [],
                },
                msg="会话不存在或暂无 WebRTC 流",
            )

        # 监管管理员可看到全部连接（含其他监管观看者，便于审核）；
        # 归属者看到的列表已排除监管观看者（计划书 §2.7）。
        #
        # 与会话状态 SSE 共用 manager.viewer_summaries() 同一构造：
        # 两条出口字段完全一致，前端只需一套 ViewerInfo 类型。
        # 旧字段 idle_seconds / idle_duration 已移除 —— 没有消费者再轮询本端点，
        # 且易变字段会破坏签名去重（见计划书 §4.4 / §10.6）。
        summaries = webrtc_mgr.viewer_summaries(include_admin=is_admin_view)
        viewers = [s.model_dump() for s in summaries]

        return success_response(
            data={
                "enabled": True,
                # 兼容字段：改造前 total_streams 表示流数，现在等于观看者连接数
                "total_streams": len(viewers),
                "active_streams": [
                    {
                        "stream_key": s.stream_key,
                        "page_index": s.page_index,
                        "state": s.state,
                        # 兼容占位（BrowserStream 仅用 active_streams.length 判断）
                        "idle_duration": 0.0,
                    }
                    for s in summaries
                ],
                "viewer_count": len(viewers),
                "viewers": viewers,
            }
        )

    except Exception as e:
        logger.error(f"获取 WebRTC 状态失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_STATUS_FAILED, msg=str(e))


@router.post(BrowserControlRouterPath.webrtc_quality, summary="设置 WebRTC 清晰度档位")
async def set_webrtc_quality(
    req: WebRTCQualityRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """设置**本观看者**的清晰度档位（原画 / 超清 / 高清 / 标清 / 流畅）

    档位切换通过「帧源按最高档采集 + 本端轨道缩放」生效，**不重建 WebRTC 连接**，
    前端无需重新 offer/answer。

    实际生效档位由「本端用户档位」与「本端页面可见性」取最省决定（见计划书 §5.18），
    因此响应里的 effective_level 可能低于请求的 level —— 这是预期行为，不是失败。
    """
    try:
        webrtc_mgr, _, owner_mid, is_admin_view = _resolve_session(browser_req)
        if webrtc_mgr is None:
            return error_response(
                code=ResponseCode.BROWSER_NOT_STARTED,
                msg="浏览器未启动或已停止",
            )

        viewer, err = _resolve_viewer(webrtc_mgr, req.viewer_id)
        if viewer is None:
            return error_response(code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE, msg=err)

        snapshot: StreamQualitySnapshot = await webrtc_mgr.set_level(
            req.viewer_id, req.level
        )
        # 档位变化会体现在观看者列表（effective_level / 帧源采集档位），推给 SSE 订阅者
        live_service.notify_session_status(owner_mid, browser_req.browser_id)
        return success_response(data=snapshot)

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
    """上报**本观看者**的页面可见性，供前端切后台 / 组件被遮挡时自动降档

    `visible=false` 本端降到最省档、`true` 回落到本端用户档位（见计划书 §5.18）。
    只影响本端，不影响其他观看者。
    """
    try:
        webrtc_mgr, _, owner_mid, is_admin_view = _resolve_session(browser_req)
        if webrtc_mgr is None:
            # 会话已回收时可见性信号已无意义：静默成功，避免前端把「切后台」
            # 误报成「浏览器未启动」而弹出重新启动的引导
            return success_response(
                data=None, msg="会话不存在或暂无 WebRTC 流，可见性信号已忽略"
            )

        viewer, err = _resolve_viewer(webrtc_mgr, req.viewer_id)
        if viewer is None:
            # 观看者已被回收（如心跳超时）时同样静默成功：可见性上报是尽力而为的
            logger.debug(f"忽略可见性上报: {err}")
            return success_response(data=None, msg="观看者不存在，可见性信号已忽略")

        snapshot: StreamQualitySnapshot = await webrtc_mgr.set_visibility(
            req.viewer_id, req.visible
        )
        # 本端生效档位变化（不可见降档 / 恢复）会体现在观看者列表，推给 SSE 订阅者
        live_service.notify_session_status(owner_mid, browser_req.browser_id)
        return success_response(data=snapshot)

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
    """暂停 / 恢复**本观看者**的视频发送（见计划书 §5.18）

    暂停时本端轨道停止取帧，画面停留在最后一帧；**不影响其他观看者**。
    当所有观看者都暂停时，后端才真正停止 screencast（浏览器侧零编码）。
    **不重建 WebRTC 连接**，恢复立即生效、无需重新协商。
    """
    try:
        webrtc_mgr, _, owner_mid, is_admin_view = _resolve_session(browser_req)
        if webrtc_mgr is None:
            return error_response(
                code=ResponseCode.BROWSER_NOT_STARTED,
                msg="浏览器未启动或已停止",
            )

        viewer, err = _resolve_viewer(webrtc_mgr, req.viewer_id)
        if viewer is None:
            return error_response(code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE, msg=err)

        snapshot: StreamQualitySnapshot = await webrtc_mgr.set_paused(
            req.viewer_id, req.paused
        )
        # 暂停态变化会体现在观看者列表（paused / 帧源引用计数），推给 SSE 订阅者
        live_service.notify_session_status(owner_mid, browser_req.browser_id)
        return success_response(data=snapshot)

    except Exception as e:
        logger.error(f"设置 WebRTC 暂停态失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_STATUS_FAILED, msg=str(e))


@router.post(BrowserControlRouterPath.webrtc_heartbeat, summary="观看者保活心跳")
async def webrtc_viewer_heartbeat(
    req: WebRTCHeartbeatRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership_or_admin),
):
    """观看者保活（**已非必需**，保留用于 SDK 兼容与手动兜底）。

    常规链路前端不再调用：观看者与会话的活性改由 **WebRTC 连接状态**判定
    （ICE/DTLS 自带保活，见计划书 §10.7）—— 连接在就一直在，暂停中的观看者
    也不会被误回收，监管场景（不订阅 SSE）同样被覆盖。

    `/webrtc/status` 仍是只读的（不刷新活跃时间），不会打穿闲置回收。
    观看者已被回收时返回业务失败码，客户端据此重新建流（新 viewer_id 再 offer）。
    """
    try:
        webrtc_mgr, _, owner_mid, is_admin_view = _resolve_session(browser_req)
        if webrtc_mgr is None:
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg="会话不存在，观看者已失效",
            )

        if not webrtc_mgr.heartbeat(req.viewer_id):
            logger.info(f"观看者心跳失败（已回收）: {req.viewer_id}")
            return error_response(
                code=ResponseCode.WEBRTC_STREAM_NOT_ACTIVE,
                msg="观看者不存在或已回收，请重新建立连接",
            )

        # 有活跃观看者 → 会话保持 ACTIVE（闲置三级软着陆因此不会误触发）。
        # 续期目标是**归属者**的会话：监管观看时管理员 mid 对应不到任何会话，
        # 若不修正则「监管看着看着就被闲置挂起」。
        await live_service.touch(
            owner_mid, browser_req.browser_id, source="webrtc_viewer"
        )
        return success_response(msg="心跳已刷新")

    except Exception as e:
        logger.error(f"观看者心跳处理失败: {e}")
        return error_response(code=ResponseCode.WEBRTC_STATUS_FAILED, msg=str(e))
