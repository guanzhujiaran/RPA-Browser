"""
WebRTCEnabledSession - 支持 WebRTC 的浏览器会话

继承自 PluginedSessionInfo，在浏览器会话层面集成 WebRTC 视频流功能。
"""

from typing import Optional
from loguru import logger

from app.services.RPA_browser.browser_session_pool.session_pool_model import PluginedSessionInfo
from app.services.RPA_browser.webrtc.stream_manager import WebRTCStreamManager


class WebRTCEnabledSession(PluginedSessionInfo):
    """
    支持 WebRTC 的浏览器会话
    
    在 PluginedSessionInfo 的基础上添加 WebRTC 视频流管理能力。
    每个 WebRTCEnabledSession 实例都有一个 WebRTCStreamManager 来管理该会话下的所有视频流。
    """
    
    def __init__(self, *args, **kwargs):
        """
        初始化 WebRTC 会话
        
        Args:
            *args: 传递给父类的参数
            **kwargs: 传递给父类的关键字参数
        """
        super().__init__(*args, **kwargs)
        self.webrtc_manager = WebRTCStreamManager(self)
        logger.info(f"WebRTCEnabledSession 已创建: mid={self.playwright_instance.mid}, browser_id={self.playwright_instance.browser_id}")
        
    async def start_webrtc_stream(self, page_index: int = 0):
        """
        启动指定页面的 WebRTC 视频流
        
        Args:
            page_index: 页面索引（从 0 开始），默认为第一个页面
            
        Returns:
            WebRTCStreamSession: 视频流会话实例
        """
        return await self.webrtc_manager.start_stream(page_index)
        
    async def get_webrtc_offer(self, page_index: int = 0) -> dict:
        """
        创建 WebRTC Offer
        
        启动指定页面的视频流并创建 SDP Offer，用于与客户端建立 WebRTC 连接。
        
        Args:
            page_index: 页面索引（从 0 开始），默认为第一个页面
            
        Returns:
            dict: 包含 sdp、type 和 stream_key 的字典
        """
        stream = await self.webrtc_manager.start_stream(page_index)
        return await stream.create_offer()
        
    async def handle_webrtc_answer(self, page_index: int, sdp: str, type: str):
        """
        处理 WebRTC Answer
        
        设置客户端发来的 SDP Answer，完成 WebRTC 握手。
        
        Args:
            page_index: 页面索引
            sdp: SDP answer 字符串
            type: SDP 类型（通常是 "answer"）
        """
        stream = self.webrtc_manager.streams.get(page_index)
        if not stream:
            raise ValueError(f"No active stream for page {page_index}")
        await stream.handle_answer(sdp, type)
        
    async def add_webrtc_ice_candidate(self, page_index: int, candidate: str, sdpMid: str, sdpMLineIndex: int):
        """
        添加 ICE Candidate
        
        Args:
            page_index: 页面索引
            candidate: ICE candidate 字符串
            sdpMid: SDP media 标识符
            sdpMLineIndex: SDP media 行索引
        """
        stream = self.webrtc_manager.streams.get(page_index)
        if not stream:
            raise ValueError(f"No active stream for page {page_index}")
        await stream.add_ice_candidate(candidate, sdpMid, sdpMLineIndex)
        
    async def close_webrtc_stream(self, page_index: int):
        """
        关闭指定页面的 WebRTC 视频流
        
        Args:
            page_index: 页面索引
        """
        await self.webrtc_manager.close_stream(page_index)
        
    async def close_all_webrtc_streams(self):
        """关闭所有 WebRTC 视频流"""
        await self.webrtc_manager.close_all_streams()
        
    async def close(self, page_index: Optional[int] = None):
        """
        关闭会话或指定页面
        
        重写父类方法，确保在关闭会话前先关闭所有 WebRTC 流。
        
        Args:
            page_index: 如果提供，则只关闭指定索引的页面；否则关闭整个会话
            
        Returns:
            SessionCloseResponse: 关闭响应
        """
        try:
            # 先关闭所有 WebRTC 流
            await self.close_all_webrtc_streams()
            logger.info("已关闭所有 WebRTC 流")
            
            # 再调用父类关闭
            return await super().close(page_index)
            
        except Exception as e:
            logger.error(f"关闭 WebRTCEnabledSession 时出错: {e}")
            # 即使 WebRTC 关闭失败，也尝试关闭基础会话
            return await super().close(page_index)
            
    async def force_close(self):
        """强制关闭会话"""
        try:
            # 强制关闭所有 WebRTC 流
            await self.close_all_webrtc_streams()
            logger.info("已强制关闭所有 WebRTC 流")
            
            # 再调用父类强制关闭
            return await super().force_close()
            
        except Exception as e:
            logger.error(f"强制关闭 WebRTCEnabledSession 时出错: {e}")
            return await super().force_close()
            
    @property
    def webrtc_active_streams(self) -> int:
        """获取活跃的 WebRTC 流数量"""
        return self.webrtc_manager.active_stream_count
        
    @property
    def webrtc_total_streams(self) -> int:
        """获取总的 WebRTC 流数量"""
        return self.webrtc_manager.total_stream_count
