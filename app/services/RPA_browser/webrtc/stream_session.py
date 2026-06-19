"""
WebRTCStreamSession - WebRTC 流会话（无循环引用设计）

管理单个页面的完整 WebRTC 生命周期。
关键设计变更：
- PageWebRTCState 使用 weakref 持有 stream_session，避免 page ↔ session 循环引用
- 内置 _last_activity 时间戳，支持 O(1) 闲置时长查询
- 状态机确保正确的生命周期转换
"""

from __future__ import annotations

import time
import weakref
from typing import TYPE_CHECKING

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from loguru import logger

if TYPE_CHECKING:
    from playwright.async_api import Page

from app.models.runtime.webrtc_models import (
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
        logger.debug(
            f"ICE Candidate 已添加: {parts[0]} {parts[4]}:{parts[5]} ({parts[7] if len(parts) > 7 else 'host'})"
        )

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
