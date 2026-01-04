import asyncio
import re
from typing import Dict, Optional
from dataclasses import dataclass
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
    RTCIceCandidate,
)
from av import VideoFrame
import numpy as np
from PIL import Image
import io
from loguru import logger
from app.services.RPA_browser.live_service import LiveService


@dataclass
class WebRTCStreamInfo:
    """WebRTC 流信息"""

    peer_connection: RTCPeerConnection
    mid: int
    browser_id: int
    active: bool = True


class BrowserVideoStreamTrack(VideoStreamTrack):
    """浏览器视频流轨道"""

    def __init__(self, mid: int, browser_id: int):
        super().__init__()
        self.mid = mid
        self.browser_id = browser_id
        self.active = True

    async def recv(self) -> VideoFrame:
        """接收视频帧"""
        if not self.active:
            raise Exception("Stream is not active")

        try:
            # 获取浏览器会话
            plugined_session = await LiveService.get_plugined_session(
                self.mid, self.browser_id, headless=False
            )
            page = await plugined_session.get_current_page()

            # 截取屏幕
            screenshot_bytes = await page.screenshot(
                type="jpeg", full_page=False
            )

            # 转换为 PIL Image
            image = Image.open(io.BytesIO(screenshot_bytes))

            # 转换为 numpy array
            frame_array = np.array(image)

            # 转换为 VideoFrame
            frame = VideoFrame.from_ndarray(frame_array, format="rgb24")

            # 控制帧率 (15fps)
            await asyncio.sleep(1 / 15)

            return frame

        except Exception as e:
            logger.error(f"WebRTC frame generation failed: {e}")
            # 返回黑屏帧
            black_frame = VideoFrame(width=640, height=480)
            await asyncio.sleep(1 / 15)
            return black_frame


class WebRTCService:
    """WebRTC 视频流服务"""

    # 存储活跃的 WebRTC 连接
    active_connections: Dict[str, WebRTCStreamInfo] = {}

    # 缓存 ICE candidates (在连接建立前收到 candidate 时使用)
    ice_candidate_cache: Dict[str, list] = {}

    # 缓存服务端的 ICE candidates (供前端获取)
    server_ice_candidates: Dict[str, list] = {}

    @staticmethod
    def _get_connection_key(mid: int, browser_id: int) -> str:
        """获取连接键"""
        return f"{mid}_{browser_id}"

    @staticmethod
    def cache_ice_candidate(mid: int, browser_id: int, candidate: dict):
        """缓存 ICE candidate"""
        connection_key = WebRTCService._get_connection_key(mid, browser_id)
        if connection_key not in WebRTCService.ice_candidate_cache:
            WebRTCService.ice_candidate_cache[connection_key] = []
        WebRTCService.ice_candidate_cache[connection_key].append(candidate)
        cache_size = len(WebRTCService.ice_candidate_cache[connection_key])
        logger.info(
            f"📦 Cached ICE candidate for {connection_key}, total cached: {cache_size}"
        )

    @staticmethod
    def get_cached_candidates(mid: int, browser_id: int) -> list:
        """获取并清除缓存的 ICE candidates"""
        connection_key = WebRTCService._get_connection_key(mid, browser_id)
        candidates = WebRTCService.ice_candidate_cache.pop(connection_key, [])
        if candidates:
            logger.info(
                f"📤 Retrieved {len(candidates)} cached ICE candidates for {connection_key}"
            )
        return candidates

    @staticmethod
    def clear_cached_candidates(mid: int, browser_id: int):
        """清除缓存的 ICE candidates"""
        connection_key = WebRTCService._get_connection_key(mid, browser_id)
        WebRTCService.ice_candidate_cache.pop(connection_key, None)
        logger.info(f"Cleared cached ICE candidates for {connection_key}")

    @staticmethod
    async def create_offer(mid: int, browser_id: int) -> dict:
        """创建 WebRTC offer"""
        connection_key = WebRTCService._get_connection_key(mid, browser_id)

        logger.info(f"Creating WebRTC offer for {connection_key}")

        # 检查是否已有活跃连接
        if connection_key in WebRTCService.active_connections:
            logger.info(
                f"Closing existing connection before creating new one for {connection_key}"
            )
            await WebRTCService.close_connection(mid, browser_id)

        try:
            # 创建新的 PeerConnection（局域网环境不需要 STUN）
            pc = RTCPeerConnection()
            logger.info(f"Created RTCPeerConnection for {connection_key}")

            # 设置 ICE 连接状态变化回调
            @pc.on("iceconnectionstatechange")
            def on_ice_connection_state_change():
                logger.info(
                    f"ICE connection state changed for {connection_key}: {pc.iceConnectionState}"
                )

            # 设置 ICE gathering 状态变化回调
            @pc.on("icegatheringstatechange")
            def on_ice_gathering_state_change():
                logger.info(
                    f"ICE gathering state changed for {connection_key}: {pc.iceGatheringState}"
                )

            # 设置连接状态变化回调
            @pc.on("connectionstatechange")
            def on_connection_state_change():
                logger.info(
                    f"Connection state changed for {connection_key}: {pc.connectionState}"
                )

            # 设置信令状态变化回调
            @pc.on("signalingstatechange")
            def on_signaling_state_change():
                logger.info(
                    f"Signaling state changed for {connection_key}: {pc.signalingState}"
                )

            # 🔥 关键: 监听 ICE candidates 并缓存到服务端缓存
            @pc.on("icecandidate")
            def on_ice_candidate(candidate):
                if candidate:
                    logger.info(f"🧊 Server ICE candidate collected for {connection_key}: {candidate}")
                    # 缓存服务端的 candidate，供前端获取
                    if connection_key not in WebRTCService.server_ice_candidates:
                        WebRTCService.server_ice_candidates[connection_key] = []
                    WebRTCService.server_ice_candidates[connection_key].append({
                        "candidate": candidate.to_sdp(),
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex
                    })
                else:
                    logger.info(f"🎉 Server ICE gathering complete for {connection_key}")

            # 创建视频轨道
            video_track = BrowserVideoStreamTrack(mid, browser_id)

            # 添加视频轨道到连接
            pc.addTrack(video_track)

            # 创建 offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            logger.info(
                f"Created offer SDP for {connection_key}, ICE gathering state: {pc.iceGatheringState}"
            )

            # 存储连接信息
            WebRTCService.active_connections[connection_key] = WebRTCStreamInfo(
                peer_connection=pc, mid=mid, browser_id=browser_id
            )

            # 🔧 注册到 LiveService 的直播流管理中，这样 video_streaming 字段才会显示为 true
            await LiveService.start_live_streaming(mid, browser_id)
            logger.info(f"✅ Registered WebRTC stream in LiveService for {connection_key}")

            return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

        except Exception as e:
            logger.exception(f"Failed to create WebRTC offer for {connection_key}: {e}")
            raise

    @staticmethod
    async def set_answer(mid: int, browser_id: int, answer_sdp: str) -> bool:
        """设置 WebRTC answer"""
        connection_key = WebRTCService._get_connection_key(mid, browser_id)

        if connection_key not in WebRTCService.active_connections:
            logger.warning(
                f"Cannot set answer for unknown connection: {connection_key}"
            )
            return False

        pc = WebRTCService.active_connections[connection_key].peer_connection

        # 设置远程描述
        answer = RTCSessionDescription(sdp=answer_sdp, type="answer")
        await pc.setRemoteDescription(answer)
        logger.info(f"✅ Set remote description (answer) for {connection_key}")

        # 🔧 检查连接状态
        logger.info(
            f"📊 ICE connection state after answer: {pc.iceConnectionState}, ICE gathering state: {pc.iceGatheringState}, signaling state: {pc.signalingState}"
        )

        # 🔧 关键修复: 处理缓存的 ICE candidates
        cached_candidates = WebRTCService.get_cached_candidates(mid, browser_id)

        if cached_candidates:
            logger.info(
                f"📦 Found {len(cached_candidates)} cached ICE candidates to process for {connection_key}"
            )

            for idx, candidate in enumerate(cached_candidates):
                try:
                    logger.info(
                        f"⏳ Processing cached ICE candidate {idx + 1}/{len(cached_candidates)} for {connection_key}"
                    )
                    success = await WebRTCService._add_ice_candidate_to_connection(
                        connection_key, pc, candidate
                    )
                    if success:
                        logger.info(
                            f"✅ Successfully added cached ICE candidate {idx + 1}/{len(cached_candidates)} for {connection_key}"
                        )
                    else:
                        logger.warning(
                            f"❌ Failed to add cached ICE candidate {idx + 1}/{len(cached_candidates)} for {connection_key}"
                        )
                except Exception as e:
                    logger.error(
                        f"❌ Failed to add cached ICE candidate {idx + 1}/{len(cached_candidates)}: {e}"
                    )

            # 记录最终状态
            logger.info(
                f"📊 ICE connection state after adding all candidates: {pc.iceConnectionState}"
            )
            logger.info(
                f"📊 Signaling state after adding all candidates: {pc.signalingState}"
            )
        else:
            logger.info(f"ℹ️ No cached ICE candidates found for {connection_key}")

        return True

    @staticmethod
    async def close_connection(mid: int, browser_id: int) -> bool:
        """关闭 WebRTC 连接"""
        connection_key = WebRTCService._get_connection_key(mid, browser_id)

        if connection_key not in WebRTCService.active_connections:
            logger.warning(
                f"Attempted to close non-existent connection: {connection_key}"
            )
            return False

        # 关闭连接
        pc = WebRTCService.active_connections[connection_key].peer_connection
        await pc.close()
        logger.info(f"Closed peer connection for {connection_key}")

        # 移除连接
        del WebRTCService.active_connections[connection_key]

        # 清除缓存的 ICE candidates
        WebRTCService.clear_cached_candidates(mid, browser_id)

        # 清除服务端 ICE candidates
        if connection_key in WebRTCService.server_ice_candidates:
            del WebRTCService.server_ice_candidates[connection_key]

        # 🔧 从 LiveService 的直播流管理中移除
        await LiveService._cleanup_live_stream(mid, browser_id)
        logger.info(f"✅ Cleaned up WebRTC stream from LiveService for {connection_key}")

        return True

    @staticmethod
    def get_connection_status(mid: int, browser_id: int) -> dict:
        """获取连接状态"""
        connection_key = WebRTCService._get_connection_key(mid, browser_id)

        if connection_key not in WebRTCService.active_connections:
            return {"active": False, "ice_connection_state": "closed", "signaling_state": "closed"}

        pc = WebRTCService.active_connections[connection_key].peer_connection

        return {
            "active": True,
            "ice_connection_state": pc.iceConnectionState,
            "signaling_state": pc.signalingState,
        }

    @staticmethod
    def get_server_ice_candidates(mid: int, browser_id: int) -> tuple[list, str]:
        """获取服务端的 ICE candidates"""
        connection_key = WebRTCService._get_connection_key(mid, browser_id)

        if connection_key not in WebRTCService.active_connections:
            logger.warning(f"Connection not found for {connection_key}")
            return [], "closed"

        pc = WebRTCService.active_connections[connection_key].peer_connection
        candidates = WebRTCService.server_ice_candidates.get(connection_key, [])

        logger.info(
            f"Returning {len(candidates)} server ICE candidates for {connection_key}, "
            f"ICE gathering state: {pc.iceGatheringState}"
        )

        return candidates, pc.iceGatheringState

    @staticmethod
    def _parse_ice_candidate(
        candidate_str: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> Optional[RTCIceCandidate]:
        """解析 SDP 格式的 ICE candidate 字符串

        格式示例: candidate:3569980810 1 udp 2113939711 192.168.1.100 49890 typ host
        """
        try:
            logger.debug(
                f"Parsing ICE candidate: {candidate_str}, sdp_mid={sdp_mid}, sdp_mline_index={sdp_mline_index}"
            )

            # 解析 candidate 字符串
            pattern = r"candidate:(\d+)\s+(\d+)\s+(\w+)\s+(\d+)\s+([\w\.-]+)\s+(\d+)\s+typ\s+(\w+)"
            match = re.match(pattern, candidate_str)

            if not match:
                logger.error(f"Invalid ICE candidate format: {candidate_str}")
                return None

            foundation, component, protocol, priority, ip, port, candidate_type = (
                match.groups()
            )

            logger.debug(
                f"Parsed ICE candidate - IP: {ip}:{port}, type: {candidate_type}, protocol: {protocol}"
            )

            # 创建 RTCIceCandidate 对象
            ice_candidate = RTCIceCandidate(
                component=int(component),
                foundation=foundation,
                ip=ip,
                port=int(port),
                priority=int(priority),
                protocol=protocol,
                type=candidate_type,
                sdpMid=sdp_mid,
                sdpMLineIndex=sdp_mline_index,
            )
            return ice_candidate

        except Exception as e:
            logger.error(
                f"Failed to parse ICE candidate: {e}, candidate_str: {candidate_str}"
            )
            return None

    @staticmethod
    async def add_ice_candidate(mid: int, browser_id: int, candidate: dict) -> bool:
        """添加 ICE candidate"""
        connection_key = WebRTCService._get_connection_key(mid, browser_id)

        logger.info(f"📨 Received ICE candidate for {connection_key}")
        logger.debug(f"📦 Candidate data: {candidate}")

        if connection_key not in WebRTCService.active_connections:
            # 连接不存在，缓存 candidate
            logger.warning(
                f"⚠️ Connection not found for {connection_key}, caching candidate. Candidate data: {candidate}"
            )
            WebRTCService.cache_ice_candidate(mid, browser_id, candidate)
            return True  # 返回 true 表示 candidate 已被缓存，不视为错误

        pc = WebRTCService.active_connections[connection_key].peer_connection

        # 🔧 关键修复: 检查是否已设置 remote description
        if pc.remoteDescription is None:
            # 还没有设置 remote description，先缓存 candidate
            logger.warning(
                f"⚠️ Remote description not set yet for {connection_key}, caching candidate"
            )
            WebRTCService.cache_ice_candidate(mid, browser_id, candidate)
            return True

        logger.info(
            f"➕ Adding ICE candidate to existing connection for {connection_key}, ICE state: {pc.iceConnectionState}"
        )

        return await WebRTCService._add_ice_candidate_to_connection(
            connection_key, pc, candidate
        )

    @staticmethod
    async def _add_ice_candidate_to_connection(
        connection_key: str, pc: RTCPeerConnection, candidate: dict
    ) -> bool:
        """向连接添加 ICE candidate 的内部方法"""
        try:
            # 从字典获取 candidate 字符串和元数据
            candidate_str = candidate.get("candidate", "")
            sdp_mid = candidate.get("sdpMid")
            sdp_mline_index = candidate.get("sdpMLineIndex")

            logger.debug(
                f"Adding candidate to {connection_key} - sdpMid={sdp_mid}, sdpMLineIndex={sdp_mline_index}, candidate={candidate_str}"
            )

            # 解析 ICE candidate
            ice_candidate = WebRTCService._parse_ice_candidate(
                candidate_str, sdp_mid, sdp_mline_index
            )

            if not ice_candidate:
                logger.error(f"Failed to parse ICE candidate: {candidate}")
                return False

            # 检查连接状态
            logger.debug(
                f"ICE connection state before adding candidate: {pc.iceConnectionState}"
            )
            logger.debug(f"ICE gathering state: {pc.iceGatheringState}")
            logger.debug(f"Signaling state: {pc.signalingState}")

            # 添加到连接
            await pc.addIceCandidate(ice_candidate)

            logger.info(
                f"ICE candidate added successfully for {connection_key}, new ICE state: {pc.iceConnectionState}"
            )
            return True

        except Exception as e:
            logger.exception(f"Failed to add ICE candidate for {connection_key}: {e}")
            logger.error(f"Failed candidate data: {candidate}")
            return False
