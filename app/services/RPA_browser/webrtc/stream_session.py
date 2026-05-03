"""
WebRTCStreamSession - WebRTC 流会话

管理单个页面的完整 WebRTC 生命周期，包括 PeerConnection、帧捕获和信令处理。
"""

import time
from typing import Optional, Dict, TYPE_CHECKING
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from loguru import logger

if TYPE_CHECKING:
    from playwright.async_api import Page

from app.models.runtime.webrtc_models import WebRTCStreamState, WebRTCStreamInfo, WebRTCSessionConfig
from .video_frame_producer import VideoFrameProducer
from .media_track import WebRTCMediaTrack


class PageWebRTCState:
    """
    Page 对象的 WebRTC 状态管理器
    
    封装 Page 对象相关的 WebRTC 状态，提供面向对象的访问接口。
    """
    
    def __init__(self, page: 'Page'):
        """
        初始化 Page 的 WebRTC 状态
        
        Args:
            page: Playwright Page 对象
        """
        self.page = page
        self.last_activity = time.time()
        self.stream_session: Optional['WebRTCStreamSession'] = None
        
    def update_activity(self):
        """更新活跃时间"""
        self.last_activity = time.time()
        
    @property
    def idle_duration(self) -> float:
        """获取闲置时长（秒）"""
        return time.time() - self.last_activity


class WebRTCStreamSession:
    """
    WebRTC 流会话
    
    封装单个浏览器页面的 WebRTC 视频流，管理从初始化到关闭的完整生命周期。
    每个页面对应一个独立的 WebRTCStreamSession 实例，确保流之间互不干扰。
    """
    
    def __init__(self, stream_key: str, page, config: WebRTCSessionConfig):
        """
        初始化 WebRTC 流会话
        
        Args:
            stream_key: 流的唯一标识符，格式: {mid}:{browser_id}:{page_id}
            page: Playwright Page 对象
            config: WebRTC 会话配置
        """
        self.stream_key = stream_key
        self.page = page
        self.page_id = page._webrtc_page_id  # 从 Page 对象获取 page_id
        self.config = config
        self.pc = RTCPeerConnection()
        self.producer = VideoFrameProducer(page, config)
        self.track: Optional[WebRTCMediaTrack] = None
        self.state = WebRTCStreamState.INITIALIZING
        
        # ✅ 初始化或获取 Page 的 WebRTC 状态管理器
        if not hasattr(page, '_webrtc_state'):
            page._webrtc_state = PageWebRTCState(page)
        self.webrtc_state: PageWebRTCState = page._webrtc_state
        
        # 将 session 引用附加到 state 对象上
        self.webrtc_state.stream_session = self
        
        # 注册 ICE 连接状态变更回调
        self.pc.on("iceconnectionstatechange")(self._on_ice_state_change)
        self.pc.on("connectionstatechange")(self._on_connection_state_change)
        
        logger.info(f"WebRTCStreamSession 已创建: {stream_key} (page_id={self.page_id})")
        
    async def start(self):
        """
        启动 WebRTC 流
        
        初始化帧生产者并创建视频轨道，将轨道添加到 PeerConnection。
        """
        try:
            logger.info(f"启动 WebRTC 流: {self.stream_key}")
            
            # 启动帧生产者
            await self.producer.start()
            
            # 创建视频轨道并添加到 PeerConnection
            self.track = WebRTCMediaTrack(self.producer)
            self.pc.addTrack(self.track)
            
            # 更新状态
            self.state = WebRTCStreamState.ACTIVE
            self.webrtc_state.update_activity()
            
            logger.info(f"WebRTC 流已启动: {self.stream_key}")
            
        except Exception as e:
            logger.error(f"启动 WebRTC 流失败 {self.stream_key}: {e}")
            self.state = WebRTCStreamState.ERROR
            raise
            
    async def create_offer(self) -> dict:
        """
        创建 SDP Offer
        
        Returns:
            dict: 包含 sdp、type 和 stream_key 的字典
        """
        if self.state != WebRTCStreamState.ACTIVE:
            raise RuntimeError(f"Cannot create offer in state: {self.state}")
            
        try:
            offer = await self.pc.createOffer()
            await self.pc.setLocalDescription(offer)
            
            self.webrtc_state.update_activity()
            
            return {
                "sdp": self.pc.localDescription.sdp,
                "type": self.pc.localDescription.type,
                "stream_key": self.stream_key
            }
            
        except Exception as e:
            logger.error(f"创建 Offer 失败 {self.stream_key}: {e}")
            raise
            
    async def handle_answer(self, sdp: str, type: str):
        """
        处理客户端发来的 SDP Answer
        
        Args:
            sdp: SDP answer 字符串
            type: SDP 类型（通常是 "answer"）
        """
        if self.state != WebRTCStreamState.ACTIVE:
            raise RuntimeError(f"Cannot handle answer in state: {self.state}")
            
        try:
            answer = RTCSessionDescription(sdp=sdp, type=type)
            await self.pc.setRemoteDescription(answer)
            
            self.webrtc_state.update_activity()
            logger.info(f"已设置 Remote Description: {self.stream_key}")
            
        except Exception as e:
            logger.error(f"处理 Answer 失败 {self.stream_key}: {e}")
            raise
            
    async def add_ice_candidate(self, candidate: str, sdpMid: str, sdpMLineIndex: int):
        """
        添加 ICE Candidate
        
        Args:
            candidate: ICE candidate 字符串 (格式: "candidate:foundation component protocol priority ip port typ type")
            sdpMid: SDP media 标识符
            sdpMLineIndex: SDP media 行索引
        """
        if self.state != WebRTCStreamState.ACTIVE:
            raise RuntimeError(f"Cannot add ICE candidate in state: {self.state}")
            
        try:
            # 解析 candidate 字符串
            # 格式: "candidate:foundation component protocol priority ip port typ type ..."
            # 示例: "candidate:6815297761 1 udp 659136 192.168.1.1 12345 typ host"
            if candidate.startswith("candidate:"):
                candidate = candidate[len("candidate:"):]
            
            parts = candidate.split()
            if len(parts) < 8:
                raise ValueError(f"Invalid candidate format: {candidate}")
            
            foundation = parts[0]
            component = int(parts[1])
            protocol = parts[2]
            priority = int(parts[3])
            ip = parts[4]
            port = int(parts[5])
            # parts[6] 应该是 "typ"
            candidate_type = parts[7] if len(parts) > 7 else "host"
            
            # 创建 RTCIceCandidate，使用命名参数
            ice_candidate = RTCIceCandidate(
                foundation=foundation,
                component=component,
                protocol=protocol,
                priority=priority,
                ip=ip,
                port=port,
                type=candidate_type,
                sdpMid=sdpMid,
                sdpMLineIndex=sdpMLineIndex
            )
            await self.pc.addIceCandidate(ice_candidate)
            
            self.webrtc_state.update_activity()
            logger.debug(f"ICE Candidate 已添加: {foundation} {ip}:{port} ({candidate_type})")
            
        except Exception as e:
            logger.error(f"添加 ICE Candidate 失败 {self.stream_key}: {e}")
            raise
            
    async def close(self):
        """
        关闭 WebRTC 流并清理资源
        
        停止帧生产者、关闭 PeerConnection、释放所有相关资源。
        """
        if self.state == WebRTCStreamState.CLOSED:
            logger.debug(f"WebRTC 流已经关闭: {self.stream_key}")
            return
            
        logger.info(f"关闭 WebRTC 流: {self.stream_key}")
        
        try:
            # 停止帧生产者（这会停止 screencast）
            if self.producer:
                logger.info(f"正在停止 VideoFrameProducer...")
                await self.producer.stop()
                logger.info(f"VideoFrameProducer 已停止")
                
            # 关闭 PeerConnection
            if self.pc:
                logger.info(f"正在关闭 PeerConnection...")
                await self.pc.close()
                logger.info(f"PeerConnection 已关闭")
                
            # 清除 webrtc_state 上的引用
            if self.webrtc_state:
                self.webrtc_state.stream_session = None
                
            # 更新状态
            self.state = WebRTCStreamState.CLOSED
            logger.info(f"WebRTC 流已关闭: {self.stream_key}")
            
        except Exception as e:
            logger.error(f"关闭 WebRTC 流时出错 {self.stream_key}: {e}")
            self.state = WebRTCStreamState.ERROR
            
    def _on_ice_state_change(self):
        """ICE 连接状态变更回调"""
        state = self.pc.iceConnectionState
        logger.info(f"ICE 状态变更: {state} for {self.stream_key}")
        
        self.webrtc_state.update_activity()
        
        # 如果连接失败或关闭，自动清理
        if state in ["failed", "closed", "disconnected"]:
            logger.warning(f"ICE 连接异常，准备关闭流: {self.stream_key}")
            # 注意：这里不直接调用 close()，避免递归
            # 由外部监控任务或上层逻辑处理
            
    def _on_connection_state_change(self):
        """PeerConnection 状态变更回调"""
        state = self.pc.connectionState
        logger.info(f"Connection 状态变更: {state} for {self.stream_key}")
        
        # ✅ 更新 page 级别的活跃时间
        self.webrtc_state.update_activity()
        
    def update_activity(self):
        """更新最后活动时间（用于闲置超时检测）- 更新 page 级别的时间"""
        self.webrtc_state.update_activity()
        
    @property
    def is_active(self) -> bool:
        """检查流是否处于活跃状态"""
        return self.state == WebRTCStreamState.ACTIVE
        
    @property
    def stream_info(self) -> WebRTCStreamInfo:
        """获取流信息"""
        # ✅ 从 webrtc_state 获取活跃时间
        last_activity = self.webrtc_state.last_activity
        
        return WebRTCStreamInfo(
            stream_key=self.stream_key,
            page_index=0,  # TODO: 需要从外部传入 page_index
            state=self.state,
            created_at=last_activity,  # 简化处理
            last_activity=last_activity
        )
