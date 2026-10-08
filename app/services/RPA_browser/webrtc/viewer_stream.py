"""ViewerStream - 观看者流（多观看者并发直播）

一个观看者 = **一条 PeerConnection + 一个媒体轨道**。

与改造前的 `WebRTCStreamSession` 的关键差异：

| 维度 | 改造前 | 现在 |
| --- | --- | --- |
| 与页面的关系 | 一个 page 一条流，新建即淘汰旧的 | 一个 page 一份**帧源**，可挂多个观看者 |
| 帧来源 | 独占 `VideoFrameProducer`（私有 screencast） | 共享页级帧源（唯一 screencast）的订阅槽 |
| 档位 / 暂停 | 会话级（所有端共享） | **观看者级**（各自独立） |
| `stream_key` | `{mid}:{browser_id}:page_{i}` | `{mid}:{browser_id}:page_{i}:{viewer_id}` |

详见 docs/rpa-多观看者并发直播计划书.md。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

# mDNS 候选（Chrome 的 `xxxx.local`）解析：复用 aioice 的实现与协议单例，
# 由本模块显式解析并留痕（见 add_ice_candidate 的说明）
from aioice import mdns as aioice_mdns
from aioice.ice import get_or_create_mdns_protocol
from aiortc import (
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
)
from aiortc.mediastreams import (
    VIDEO_CLOCK_RATE,
    VIDEO_TIME_BASE,
    MediaStreamError,
)
from loguru import logger

from app.models.runtime.webrtc_models import (
    StreamQualityLevelEnum,
    ViewerStreamInfo,
    WebRTCSessionConfig,
    WebRTCStreamState,
)
from .video_frame_producer import FrameSlot, clone_video_frame


class ViewerMediaTrack(VideoStreamTrack):
    """观看者媒体轨道。

    从帧源订阅槽取帧，按**本端**档位做缩放与抽帧，再交给 aiortc 编码发送。

    帧源按「所有观看者的最高档」采集，因此本端只会**下调**，不会上调；
    当本端档位等于帧源档位时不做任何处理（单人观看即为此情形，零开销）。

    时间戳策略沿用改造前的做法：**以墙钟时间为准**，不使用父类 `next_timestamp()`
    —— aiortc 的 `VIDEO_PTIME` 硬编码 1/30，与降档后的真实出帧节奏脱钩。
    """

    def __init__(
        self,
        slot: FrameSlot,
        config: WebRTCSessionConfig,
        level: StreamQualityLevelEnum,
    ):
        super().__init__()
        self._slot = slot
        self._config = config
        self._level = level
        self._paused = False
        self._resume_event = asyncio.Event()
        self._resume_event.set()

        self._start_time: float | None = None
        self._last_pts: int = 0
        self._next_send_at: float = 0.0

        # 出帧统计（供排障：本端被抽掉了多少帧）
        self.sent_frames = 0
        self.throttled_frames = 0
        logger.debug(f"ViewerMediaTrack 已初始化（档位={level.value}）")

    # ── 观看者级状态 ──

    def set_level(self, level: StreamQualityLevelEnum) -> None:
        """更新本端生效档位（缩放 + 抽帧节拍随之改变）。"""
        if self._level == level:
            return
        self._level = level
        # 立即生效：重置抽帧节拍，避免切档后仍按旧间隔等待
        self._next_send_at = 0.0
        logger.debug(f"观看者轨道档位变更: {level.value}")

    def set_paused(self, paused: bool) -> None:
        """本端暂停 / 恢复出帧（不影响其他观看者）。"""
        if self._paused == paused:
            return
        self._paused = paused
        if paused:
            self._resume_event.clear()
        else:
            self._resume_event.set()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def level(self) -> StreamQualityLevelEnum:
        return self._level

    async def recv(self):  # noqa: D102 - 覆写父类
        params = self._config.params_for(self._level)

        while True:
            if self._paused:
                # 暂停：挂起而非返回 None —— 返回 None 会被判定为「轨道结束」。
                await self._resume_event.wait()
                if self.readyState != "live":
                    raise MediaStreamError("轨道已不再活跃")
                continue

            item = await self._slot.next_frame()
            if item is None:
                # 帧源已停止（页面关闭 / 会话释放）：如实结束轨道
                logger.debug("帧源已停止，观看者轨道结束")
                raise MediaStreamError("帧源已停止")

            # 帧源可能在此期间被暂停，或本轨道已被暂停
            if self._paused:
                continue

            if self.readyState != "live":
                raise MediaStreamError("轨道已不再活跃")

            now = time.monotonic()
            if now < self._next_send_at:
                # 抽帧：本端帧率低于帧源采集帧率，丢弃该帧（零编码成本）
                self.throttled_frames += 1
                continue
            self._next_send_at = now + params.frame_interval
            break

        frame, shared = item
        frame = self._prepare_frame(frame, shared, params)

        now = time.monotonic()
        if self._start_time is None:
            self._start_time = now
        pts = int((now - self._start_time) * VIDEO_CLOCK_RATE)
        if pts <= self._last_pts:
            pts = self._last_pts + 1
        self._last_pts = pts

        frame.pts = pts
        frame.time_base = VIDEO_TIME_BASE
        self.sent_frames += 1
        return frame

    def _prepare_frame(self, frame, shared: bool, params):
        """多观看者共享帧必须克隆；缩放仅作兜底。

        帧源广播时已按**槽位档位**分组缩放（见计划书 §10.8），因此这里
        常规路径只走克隆分支；仅当槽位与轨道档位短暂不同步
        （切档瞬间槽位尚未更新）时才会真正触发缩放。

        克隆的原因见计划书 §2.6：`recv()` 会**就地**改写 `frame.pts`，
        而广播模式下多个观看者拿到的是同一个帧对象。
        `reformat()` 在尺寸确有变化时必然返回新对象，此时无需再克隆。
        """
        size = params.size
        if size is not None:
            # H264 要求偶数宽高
            width = max(2, size["width"] - size["width"] % 2)
            height = max(2, size["height"] - size["height"] % 2)
            if (frame.width, frame.height) != (width, height):
                try:
                    return frame.reformat(width=width, height=height, format="yuv420p")
                except Exception as e:  # noqa: BLE001 - 缩放失败不应打断整条流
                    logger.warning(f"帧缩放失败，回退原帧: {e}")

        if shared:
            return clone_video_frame(frame)
        return frame


class ViewerStream:
    """观看者流：一条 PeerConnection + 观看者级档位 / 暂停状态。

    信令部分迁移自改造前的 `WebRTCStreamSession`（含 mDNS 候选的显式解析与留痕）。
    """

    def __init__(
        self,
        viewer_id: str,
        stream_key: str,
        page_index: int,
        config: WebRTCSessionConfig,
        slot: FrameSlot,
        client_ip: str = "",
        client_device: str = "",
        client_device_type: str = "",
        client_browser_version: str = "",
        client_ip_region: str = "",
        client_ip_isp: str = "",
        is_admin: bool = False,
    ):
        self.viewer_id = viewer_id
        self.stream_key = stream_key
        self.page_index = page_index
        self.config = config
        # 帧源订阅槽：由 WebRTCStreamManager 在创建/销毁时负责订阅与反订阅，
        # 观看者本身不持有帧源引用（避免 viewer ↔ source 循环引用）
        self.slot = slot

        # ── 身份信息（供浏览器归属者判断「是谁在看」，见计划书 §2.7）──
        # client_ip 由网关解析 nginx 的 X-Real-IP / X-Forwarded-For 后注入；取不到为空串
        self.client_ip = client_ip
        self.client_device = client_device
        # 设备类型稳定码（desktop / mobile / tablet）与浏览器大版本：来自 User-Agent
        self.client_device_type = client_device_type
        self.client_browser_version = client_browser_version
        # IP 属地 / 运营商（be-message GeoIP RPC 一次调用返回；失败为空串）
        self.client_ip_region = client_ip_region
        self.client_ip_isp = client_ip_isp
        # 监管管理员观看：对归属者完全隐藏（不进人数、不进列表）
        self.is_admin = is_admin
        self.connected_at = time.time()

        self.pc = RTCPeerConnection()

        # 观看者级档位：用户档位 + 本端可见性（两者互不覆盖，取最省）
        self._level: StreamQualityLevelEnum = StreamQualityLevelEnum.HIGH
        self._visibility_low: bool = False
        self._paused: bool = False

        self._state: WebRTCStreamState = WebRTCStreamState.INITIALIZING
        self._last_activity: float = time.time()
        # 被跳过的 mDNS host 候选数（跨网段 / 容器部署的常见情况，见 add_ice_candidate）
        self._mdns_skipped_candidates: int = 0

        self.track = ViewerMediaTrack(slot, config, self._level)

        self.pc.on("iceconnectionstatechange")(self._on_ice_state_change)
        self.pc.on("connectionstatechange")(self._on_connection_state_change)

        logger.debug(f"ViewerStream 已创建: {stream_key} (page_index={page_index})")

    # ── 状态 ──

    @property
    def state(self) -> WebRTCStreamState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == WebRTCStreamState.ACTIVE

    @property
    def idle_seconds(self) -> float:
        """距最后一次心跳的时长（秒）—— O(1）"""
        return time.time() - self._last_activity

    def touch(self) -> None:
        """刷新活跃时间（信令与心跳都会调用）。"""
        self._last_activity = time.time()

    # ── 观看者级档位 / 暂停 ──

    @property
    def level(self) -> StreamQualityLevelEnum:
        """用户档位"""
        return self._level

    @property
    def effective_level(self) -> StreamQualityLevelEnum:
        """本端生效档位：用户档位与本端可见性取最省"""
        if self._visibility_low:
            return StreamQualityLevelEnum.LOW
        return self._level

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_degraded(self) -> bool:
        """是否因本端不可见而被降档"""
        return self._visibility_low

    def set_level(self, level: StreamQualityLevelEnum) -> None:
        """设置用户档位（幂等）。本端生效档位随之刷新。"""
        if self._level == level:
            return
        self._level = level
        self.track.set_level(self.effective_level)
        logger.info(
            f"观看者档位变更: {level.value} (生效={self.effective_level.value}, "
            f"viewer={self.viewer_id})"
        )

    def set_visibility(self, visible: bool) -> None:
        """本端页面可见性信号（幂等）：不可见降到最省档，恢复回落到用户档位。"""
        low = not visible
        if self._visibility_low == low:
            return
        self._visibility_low = low
        self.track.set_level(self.effective_level)
        logger.debug(
            f"观看者可见性变化: visible={visible} "
            f"(生效={self.effective_level.value}, viewer={self.viewer_id})"
        )

    def set_paused(self, paused: bool) -> None:
        """本端暂停 / 恢复出帧（幂等）。不影响其他观看者。"""
        if self._paused == paused:
            return
        self._paused = paused
        self.track.set_paused(paused)
        logger.info(
            f"观看者{'暂停' if paused else '恢复'}出帧: viewer={self.viewer_id}"
        )

    def info(self) -> ViewerStreamInfo:
        """观看者信息快照（供 /webrtc/status 的观看者列表）。"""
        return ViewerStreamInfo(
            viewer_id=self.viewer_id,
            stream_key=self.stream_key,
            page_index=self.page_index,
            state=self._state,
            paused=self._paused,
            level=self._level.value,
            effective_level=self.effective_level.value,
            last_activity=self._last_activity,
            connected_at=self.connected_at,
            client_ip=self.client_ip,
            client_device=self.client_device,
            client_device_type=self.client_device_type,
            client_browser_version=self.client_browser_version,
            client_ip_region=self.client_ip_region,
            client_ip_isp=self.client_ip_isp,
            is_admin=self.is_admin,
        )

    @property
    def client_summary(self) -> str:
        """客户端信息单行摘要（日志用）—— 与 `/webrtc/status` 的字段同源"""
        return self.info().client_summary

    @property
    def watch_seconds(self) -> int:
        """已观看时长（秒）—— 断开 / 回收日志展示用"""
        return self.info().watch_seconds

    # ── 生命周期 ──

    async def start(self) -> None:
        """启动观看者流：把媒体轨道挂到自己的 PeerConnection 上。"""
        try:
            logger.debug(f"启动观看者流: {self.stream_key}")
            self.pc.addTrack(self.track)
            self._state = WebRTCStreamState.ACTIVE
            self.touch()
            logger.debug(f"观看者流已启动: {self.stream_key}")
        except Exception as e:
            logger.error(f"启动观看者流失败 {self.stream_key}: {e}")
            self._state = WebRTCStreamState.ERROR
            raise

    async def close(self) -> None:
        """关闭观看者流并释放资源（幂等）。**只影响本端**。"""
        if self._state == WebRTCStreamState.CLOSED:
            logger.debug(f"观看者流已关闭，跳过: {self.stream_key}")
            return

        logger.debug(f"关闭观看者流: {self.stream_key}")
        try:
            if self.pc:
                await self.pc.close()
            self._state = WebRTCStreamState.CLOSED
            logger.debug(f"观看者流已关闭: {self.stream_key}")
        except Exception as e:
            logger.error(f"关闭观看者流时出错 {self.stream_key}: {e}")
            self._state = WebRTCStreamState.ERROR

    # ── 信令处理 ──

    async def create_offer(self) -> dict:
        """创建 SDP Offer。

        Raises:
            RuntimeError: 流不在 ACTIVE 状态
        """
        if self._state != WebRTCStreamState.ACTIVE:
            raise RuntimeError(f"无法在 {self._state.value} 状态创建 Offer")

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        self.touch()

        return {
            "sdp": self.pc.localDescription.sdp,
            "type": self.pc.localDescription.type,
            "stream_key": self.stream_key,
        }

    async def handle_answer(self, sdp: str, type: str) -> None:
        """处理客户端 SDP Answer。"""
        if self._state != WebRTCStreamState.ACTIVE:
            raise RuntimeError(f"无法在 {self._state.value} 状态处理 Answer")

        answer = RTCSessionDescription(sdp=sdp, type=type)
        await self.pc.setRemoteDescription(answer)
        self.touch()
        logger.debug(f"已设置 Remote Description: {self.stream_key}")

    async def add_ice_candidate(
        self, candidate: str, sdpMid: str, sdpMLineIndex: int
    ) -> bool:
        """添加 ICE Candidate。

        解析 "candidate:..." 格式字符串为 RTCIceCandidate 对象。

        Returns:
            bool: True=已交给 aiortc；False=该候选被跳过（无法解析的 mDNS host 候选）
        """
        if self._state != WebRTCStreamState.ACTIVE:
            raise RuntimeError(f"无法在 {self._state.value} 状态添加 ICE Candidate")

        candidate = candidate.removeprefix("candidate:")

        parts = candidate.split()
        if len(parts) < 8:
            raise ValueError(f"无效的 candidate 格式: {candidate}")

        # Chrome 默认用 mDNS 混淆 host 候选（形如 xxxx.local）。aioice 虽然会自己解析，
        # 但失败时只打一条 stdlib 日志（不进 loguru），出问题时完全不可见；这里显式解析并留痕：
        # 成功则把**解析后的 IP** 交给 aiortc（避免重复解析）。
        #
        # 为什么「解析失败」不算错误：mDNS 混淆的 host 候选只在「对端与本服务处在同一广播域」
        # 时才有意义（同机 / 同网段）。跨网段、容器化、前后端不同机器的部署下它必然解析不出来，
        # 而连通性由 srflx / relay 候选兜底 —— 这是**可预期的正常情况**，不是故障。
        ip = parts[4]
        if aioice_mdns.is_mdns_hostname(ip):
            resolved = await self._resolve_mdns_candidate(ip)
            if resolved is None:
                self._mdns_skipped_candidates += 1
                logger.debug(
                    f"跳过无法解析的 mDNS host 候选 {ip} (mid={sdpMid} "
                    f"mline={sdpMLineIndex} stream={self.stream_key})："
                    "对端 mDNS 应答不可达（跨网段 / 容器部署的常见情况），"
                    "该 host 候选不可用，连通性由 srflx / relay 候选兜底"
                )
                return False
            logger.debug(f"mDNS 候选 {ip} 解析为 {resolved}")
            ip = resolved

        ice_candidate = RTCIceCandidate(
            foundation=parts[0],
            component=int(parts[1]),
            protocol=parts[2],
            priority=int(parts[3]),
            ip=ip,
            port=int(parts[5]),
            type=parts[7] if len(parts) > 7 else "host",
            sdpMid=sdpMid,
            sdpMLineIndex=sdpMLineIndex,
        )
        await self.pc.addIceCandidate(ice_candidate)
        self.touch()
        # 保留 DEBUG：ICE 卡在 checking 需要逐条核对「浏览器候选是否真的送到」时，
        # 打开 LOG_LEVEL=DEBUG 即可看到每一条候选
        logger.debug(
            f"ICE Candidate 已添加: {ip}:{parts[5]} "
            f"({parts[7] if len(parts) > 7 else 'host'}) "
            f"mid={sdpMid} mline={sdpMLineIndex} stream={self.stream_key}"
        )
        return True

    async def _resolve_mdns_candidate(
        self, hostname: str, timeout: float = 2.0
    ) -> str | None:
        """解析 mDNS 候选主机名（`xxxx.local`），超时/失败返回 None

        复用 aioice 的协议单例（`get_or_create_mdns_protocol`），避免每个候选都新开一个
        5353 组播 socket；解析本身在 aioice 里带 1s 超时，这里再包一层 2s 保险。
        """
        try:
            protocol = await get_or_create_mdns_protocol(self)
            return await asyncio.wait_for(
                protocol.resolve(hostname, timeout=timeout), timeout=timeout + 0.5
            )
        except Exception as e:
            logger.warning(f"mDNS 解析异常 {hostname}: {e}")
            return None

    # ── 连接状态回调 ──

    def _on_ice_state_change(self) -> None:
        state = self.pc.iceConnectionState
        logger.info(f"ICE 状态变更: {state} for {self.stream_key}")
        if state == "failed" and self._mdns_skipped_candidates:
            # 平时只记 WARNING；真的连不上时把「跳过了多少 mDNS 候选」摆出来，
            # 便于一眼区分「mDNS 拖累」与「压根没收到候选 / NAT 打不通」
            logger.error(
                f"ICE 连接失败，且此前跳过了 {self._mdns_skipped_candidates} 个无法解析的 "
                f"mDNS host 候选 ({self.stream_key})：若对端与服务同网段，"
                "可检查 5353/udp mDNS 是否被防火墙或容器网络阻断，"
                "或在前端浏览器侧关闭 WebRTC 的 mDNS 混淆"
                "（chrome://flags/#enable-webrtc-hide-local-ips-with-mdns）"
            )
        self.touch()

    def _on_connection_state_change(self) -> None:
        state = self.pc.connectionState
        logger.info(f"Connection 状态变更: {state} for {self.stream_key}")
        self.touch()


__all__ = ["ViewerStream", "ViewerMediaTrack"]
