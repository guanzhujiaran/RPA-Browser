"""
WebRTCStreamSession - WebRTC 流会话（无循环引用设计）

管理单个页面的完整 WebRTC 生命周期。
关键设计变更：
- PageWebRTCState 使用 weakref 持有 stream_session，避免 page ↔ session 循环引用
- 内置 _last_activity 时间戳，支持 O(1) 闲置时长查询
- 状态机确保正确的生命周期转换
"""

from __future__ import annotations

import asyncio
import time
import weakref
from typing import TYPE_CHECKING

# mDNS 候选（Chrome 的 `xxxx.local`）解析：复用 aioice 的实现与协议单例，
# 由本模块显式解析并留痕（见 add_ice_candidate 的说明）
from aioice import mdns as aioice_mdns
from aioice.ice import get_or_create_mdns_protocol
from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription
from loguru import logger

if TYPE_CHECKING:
    from playwright.async_api import Page

from app.models.runtime.webrtc_models import (
    StreamQualityLevelEnum,
    WebRTCStreamState,
    WebRTCStreamInfo,
    WebRTCSessionConfig,
)
from .video_frame_producer import VideoFrameProducer
from .media_track import WebRTCMediaTrack


class PageWebRTCState:
    """
    Page 对象的 WebRTC 状态管理器（轻量级，无循环引用）

    使用 weakref 持有 stream_session，避免:
      page → _webrtc_state → stream_session → page 循环引用
    """

    __slots__ = ('page_ref', '_last_activity', '_stream_session_ref')

    def __init__(self, page: 'Page'):
        self.page_ref = weakref.ref(page)
        self._last_activity = time.time()
        self._stream_session_ref: weakref.ReferenceType | None = None

    def update_activity(self):
        """更新活跃时间"""
        self._last_activity = time.time()

    @property
    def idle_duration(self) -> float:
        """闲置时长（秒）"""
        return time.time() - self._last_activity

    @property
    def stream_session(self):
        """获取 stream_session（弱引用，可能返回 None）"""
        return self._stream_session_ref() if self._stream_session_ref is not None else None

    @stream_session.setter
    def stream_session(self, value):
        """设置 stream_session（以弱引用持有）"""
        self._stream_session_ref = None if value is None else weakref.ref(value)


class WebRTCStreamSession:
    """
    WebRTC 流会话

    封装单个浏览器页面的 WebRTC 视频流，管理从初始化到关闭的完整生命周期。
    使用状态机确保正确的生命周期转换：

        INITIALIZING ──start()──▶ ACTIVE ──close()──▶ CLOSED
              │                      │
              └──── error ────▶ ERROR
    """

    # 合法状态转换表
    _STATE_TRANSITIONS: dict[WebRTCStreamState, set[WebRTCStreamState]] = {
        WebRTCStreamState.INITIALIZING: {WebRTCStreamState.ACTIVE, WebRTCStreamState.ERROR},
        WebRTCStreamState.ACTIVE: {WebRTCStreamState.CLOSED, WebRTCStreamState.ERROR},
        WebRTCStreamState.ERROR: {WebRTCStreamState.CLOSED},
        WebRTCStreamState.CLOSED: set(),  # 终态，不可转换
    }

    def __init__(
        self,
        stream_key: str,
        page: 'Page',
        config: WebRTCSessionConfig,
        page_index: int = 0,
    ):
        """
        初始化 WebRTC 流会话

        Args:
            stream_key: 流唯一标识符 "{mid}:{browser_id}:page_{page_index}"
            page: Playwright Page 对象
            config: WebRTC 会话配置
            page_index: 页面索引
        """
        self.stream_key = stream_key
        self.page = page
        self.page_index = page_index
        self.config = config

        self.pc = RTCPeerConnection()
        self.producer = VideoFrameProducer(page, config)
        self.track: WebRTCMediaTrack | None = None

        self._state: WebRTCStreamState = WebRTCStreamState.INITIALIZING
        self._last_activity: float = time.time()
        # 清晰度档位（见计划书 §5.18）：用户档位 + 两个自动降档来源 + 用户暂停开关
        self._level: StreamQualityLevelEnum = StreamQualityLevelEnum.HIGH
        self._degraded: bool = False
        self._visibility_low: bool = False
        self._paused: bool = False

        # 初始化或获取 Page 的 WebRTC 状态管理器
        if not hasattr(page, '_webrtc_state'):
            page._webrtc_state = PageWebRTCState(page)
        self.webrtc_state: PageWebRTCState = page._webrtc_state

        # 将 session 引用以弱引用方式附加到 state（无循环引用）
        self.webrtc_state.stream_session = self

        # 注册 ICE / Connection 状态变更回调
        self.pc.on("iceconnectionstatechange")(self._on_ice_state_change)
        self.pc.on("connectionstatechange")(self._on_connection_state_change)

        logger.info(
            f"WebRTCStreamSession 已创建: {stream_key} (page_index={page_index})"
        )

    # ── 状态管理 ──

    @property
    def state(self) -> WebRTCStreamState:
        return self._state

    @state.setter
    def state(self, new_state: WebRTCStreamState):
        """带校验的状态转换"""
        allowed = self._STATE_TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            logger.warning(
                f"非法的状态转换: {self._state.value} → {new_state.value}, "
                f"stream_key={self.stream_key}"
            )
        self._state = new_state

    @property
    def is_active(self) -> bool:
        return self._state == WebRTCStreamState.ACTIVE

    @property
    def idle_duration(self) -> float:
        """闲置时长（秒）—— O(1)"""
        return time.time() - self._last_activity

    def _touch(self):
        """更新活动时间"""
        self._last_activity = time.time()

    def touch(self):
        """公开的活跃刷新（供 LiveService.touch 调用）"""
        self._touch()
        self.webrtc_state.update_activity()

    async def set_level(self, level: StreamQualityLevelEnum) -> None:
        """设置用户清晰度档位（幂等，见 §5.18）"""
        if self._level == level:
            return
        self._level = level
        if self.producer:
            await self.producer.set_level(level)
        logger.info(f"WebRTC 流用户档位: {level.value}, stream={self.stream_key}")

    async def set_visibility(self, visible: bool) -> None:
        """前端可见性信号（幂等）：不可见降档、恢复可见时回落到用户档位（见 §5.18）"""
        low = not visible
        if self._visibility_low == low:
            return
        self._visibility_low = low
        if self.producer:
            await self.producer.set_visibility(visible)
        logger.info(
            f"WebRTC 流可见性变化: visible={visible}, stream={self.stream_key}"
        )

    async def set_degraded(self, degraded: bool):
        """闲置生命周期降档（幂等）：降档时降低 screencast 质量并限帧（见 §5.15）。"""
        if self._degraded == degraded:
            return
        self._degraded = degraded
        if self.producer:
            await self.producer.set_degraded(degraded)
        logger.info(
            f"WebRTC 流{'进入闲置降档' if degraded else '解除闲置降档'}: {self.stream_key}"
        )

    async def set_paused(self, paused: bool) -> None:
        """暂停 / 恢复出帧（幂等，见 §5.18）：暂停时带宽与 CPU 归零，且不重建连接。"""
        if self._paused == paused:
            return
        self._paused = paused
        if self.producer:
            await self.producer.set_paused(paused)
        logger.info(f"WebRTC 流{'暂停' if paused else '恢复'}出帧: {self.stream_key}")

    @property
    def is_paused(self) -> bool:
        """是否处于用户暂停态"""
        return self._paused

    @property
    def level(self) -> StreamQualityLevelEnum:
        """用户档位"""
        return self._level

    @property
    def effective_level(self) -> StreamQualityLevelEnum:
        """生效档位（用户档位与各自动降档来源取最省）"""
        if self.producer:
            return self.producer.effective_level()
        return self._level

    @property
    def is_degraded(self) -> bool:
        """是否被自动降档（页面不可见 / 会话闲置）"""
        return self._degraded or self._visibility_low

    # ── 生命周期 ──

    async def start(self):
        """启动 WebRTC 流：初始化帧捕获并创建视频轨道"""
        try:
            logger.info(f"启动 WebRTC 流: {self.stream_key}")
            await self.producer.start()
            self.track = WebRTCMediaTrack(self.producer)
            self.pc.addTrack(self.track)
            self.state = WebRTCStreamState.ACTIVE
            self._touch()
            self.webrtc_state.update_activity()
            logger.info(f"WebRTC 流已启动: {self.stream_key}")
        except Exception as e:
            logger.error(f"启动 WebRTC 流失败 {self.stream_key}: {e}")
            self.state = WebRTCStreamState.ERROR
            raise

    async def close(self):
        """关闭 WebRTC 流并清理所有资源（幂等）"""
        if self._state == WebRTCStreamState.CLOSED:
            logger.debug(f"WebRTC 流已关闭，跳过: {self.stream_key}")
            return

        logger.info(f"关闭 WebRTC 流: {self.stream_key}")
        try:
            if self.producer:
                await self.producer.stop()
            if self.pc:
                await self.pc.close()
            # 清除 webrtc_state 上的弱引用
            if self.webrtc_state:
                self.webrtc_state.stream_session = None
            self.state = WebRTCStreamState.CLOSED
            logger.info(f"WebRTC 流已关闭: {self.stream_key}")
        except Exception as e:
            logger.error(f"关闭 WebRTC 流时出错 {self.stream_key}: {e}")
            self.state = WebRTCStreamState.ERROR

    # ── 信令处理 ──

    async def create_offer(self) -> dict:
        """
        创建 SDP Offer

        Returns:
            {"sdp": str, "type": str, "stream_key": str}

        Raises:
            RuntimeError: 流不在 ACTIVE 状态
        """
        if self._state != WebRTCStreamState.ACTIVE:
            raise RuntimeError(f"无法在 {self._state.value} 状态创建 Offer")

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        self._touch()
        self.webrtc_state.update_activity()

        return {
            "sdp": self.pc.localDescription.sdp,
            "type": self.pc.localDescription.type,
            "stream_key": self.stream_key,
        }

    async def handle_answer(self, sdp: str, type: str):
        """处理客户端 SDP Answer"""
        if self._state != WebRTCStreamState.ACTIVE:
            raise RuntimeError(f"无法在 {self._state.value} 状态处理 Answer")

        answer = RTCSessionDescription(sdp=sdp, type=type)
        await self.pc.setRemoteDescription(answer)
        self._touch()
        self.webrtc_state.update_activity()
        logger.info(f"已设置 Remote Description: {self.stream_key}")

    async def add_ice_candidate(
        self, candidate: str, sdpMid: str, sdpMLineIndex: int
    ):
        """
        添加 ICE Candidate

        解析 "candidate:..." 格式字符串为 RTCIceCandidate 对象。
        """
        if self._state != WebRTCStreamState.ACTIVE:
            raise RuntimeError(f"无法在 {self._state.value} 状态添加 ICE Candidate")

        # 解析 candidate 字符串
        candidate = candidate.removeprefix("candidate:")

        parts = candidate.split()
        if len(parts) < 8:
            raise ValueError(f"无效的 candidate 格式: {candidate}")

        # Chrome 默认用 mDNS 混淆 host 候选（形如 xxxx.local）。aioice 虽然会自己解析，
        # 但失败时只打一条 stdlib 日志（不进 loguru），出问题时完全不可见；这里显式解析并留痕：
        # 成功则把**解析后的 IP** 交给 aiortc（避免重复解析），失败则明确报错而不是静默丢弃。
        ip = parts[4]
        if aioice_mdns.is_mdns_hostname(ip):
            resolved = await self._resolve_mdns_candidate(ip)
            if resolved is None:
                raise RuntimeError(
                    f"mDNS 候选无法解析: {ip}"
                    "（对端 mDNS 应答不可达：同网段/同机应能解析，解析不了就只剩 srflx/TURN）"
                )
            logger.info(f"mDNS 候选 {ip} 解析为 {resolved}")
            ip = resolved

        ice_candidate = RTCIceCandidate(
            foundation=parts[0],
            component=int(parts[1]),
            protocol=parts[2],
            priority=int(parts[3]),
            ip=parts[4],
            port=int(parts[5]),
            type=parts[7] if len(parts) > 7 else "host",
            sdpMid=sdpMid,
            sdpMLineIndex=sdpMLineIndex,
        )
        await self.pc.addIceCandidate(ice_candidate)
        self._touch()
        self.webrtc_state.update_activity()
        # 提到 INFO：ICE 卡在 checking 时，这条日志是判断「浏览器候选是否真的送到」
        # 的唯一线索（Chrome 默认用 mDNS 混淆 host 候选，形如 xxxx.local，由 aioice 解析）
        logger.info(
            f"ICE Candidate 已添加: {parts[4]}:{parts[5]} "
            f"({parts[7] if len(parts) > 7 else 'host'}) "
            f"mid={sdpMid} mline={sdpMLineIndex} stream={self.stream_key}"
        )

    async def _resolve_mdns_candidate(self, hostname: str, timeout: float = 2.0) -> str | None:
        """解析 mDNS 候选主机名（`xxxx.local`），超时/失败返回 None

        复用 aioice 的协议单例（`get_or_create_mdns_protocol`），避免每个候选都新开一个
        5353 组播 socket；解析本身在 aioice 里带 1s 超时，这里再包一层 2s 保险。
        """
        try:
            protocol = await get_or_create_mdns_protocol(self)
            return await asyncio.wait_for(protocol.resolve(hostname, timeout=timeout), timeout=timeout + 0.5)
        except Exception as e:
            logger.warning(f"mDNS 解析异常 {hostname}: {e}")
            return None

    # ── 连接状态回调 ──

    def _on_ice_state_change(self):
        """ICE 连接状态变更回调"""
        state = self.pc.iceConnectionState
        logger.info(f"ICE 状态变更: {state} for {self.stream_key}")
        self._touch()
        self.webrtc_state.update_activity()

    def _on_connection_state_change(self):
        """PeerConnection 状态变更回调"""
        state = self.pc.connectionState
        logger.info(f"Connection 状态变更: {state} for {self.stream_key}")
        self._touch()
        self.webrtc_state.update_activity()

    # ── 信息查询 ──

    @property
    def stream_info(self) -> WebRTCStreamInfo:
        """获取流信息快照"""
        return WebRTCStreamInfo(
            stream_key=self.stream_key,
            page_index=self.page_index,
            state=self._state,
            created_at=self._last_activity,
            last_activity=self._last_activity,
        )
