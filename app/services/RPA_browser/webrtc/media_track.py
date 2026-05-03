"""
WebRTCMediaTrack - WebRTC 视频媒体轨道

实现 aiortc 的 VideoStreamTrack 接口，将 VideoFrameProducer 产生的帧
提供给 WebRTC PeerConnection。
"""

from aiortc import VideoStreamTrack
from loguru import logger

from .video_frame_producer import VideoFrameProducer


class WebRTCMediaTrack(VideoStreamTrack):
    """
    WebRTC 视频媒体轨道
    
    继承自 aiortc.VideoStreamTrack，从 VideoFrameProducer 获取视频帧
    并通过 WebRTC 连接发送给客户端。VideoStreamTrack 提供了自动的时间戳管理。
    """
    
    def __init__(self, producer: VideoFrameProducer):
        """
        初始化媒体轨道
        
        Args:
            producer: VideoFrameProducer 实例，提供视频帧
        """
        super().__init__()
        self.producer = producer
        logger.info("WebRTCMediaTrack 已初始化")
        
    async def recv(self):
        """
        接收下一帧
        
        由 aiortc 内部调用，当需要发送新帧时触发。
        使用父类的 next_timestamp() 方法获取正确的时间戳和时基。
        
        Returns:
            av.VideoFrame: 带有时间戳的视频帧
            
        Raises:
            Exception: 如果获取帧失败或生产者已停止
        """
        try:
            # 从生产者获取下一帧
            frame = await self.producer.get_next_frame()
            
            if frame is None:
                # 生产者已停止，抛出异常以结束轨道
                raise StopIteration("VideoFrameProducer 已停止")
            
            # 使用父类的方法获取时间戳和时基（自动处理 90kHz 时钟）
            pts, time_base = await self.next_timestamp()
            
            # 设置帧的时间戳和时基
            frame.pts = pts
            frame.time_base = time_base
            
            return frame
            
        except StopIteration:
            logger.info("WebRTCMediaTrack 收到 StopIteration，轨道结束")
            raise
        except Exception as e:
            logger.error(f"WebRTCMediaTrack recv 错误: {e}")
            raise
