"""
VideoFrameProducer - 视频帧生产者

负责从 Playwright screencast API 捕获页面帧，并将其转换为 av.VideoFrame 格式。
所有 CPU 密集型操作（JPEG 解码、格式转换）均在线程池中执行，避免阻塞事件循环。
"""

import asyncio
import io
from typing import Optional
import av
from PIL import Image
from loguru import logger
from playwright.async_api import Page

from app.models.runtime.webrtc_models import WebRTCSessionConfig


class VideoFrameProducer:
    """
    视频帧生产者
    
    使用 Playwright 的 page.screencast.start() API 捕获页面帧，
    并通过异步队列提供给消费者（WebRTCMediaTrack）。
    """
    
    def __init__(self, page:Page, config: WebRTCSessionConfig):
        """
        初始化视频帧生产者
        
        Args:
            page: Playwright Page 对象
            config: WebRTC 会话配置
        """
        self.page = page
        self.config = config
        self.frame_queue: asyncio.Queue = asyncio.Queue(maxsize=config.frame_queue_size)
        self.screencast_session = None
        self._is_running = False
        self._last_frame: Optional[av.VideoFrame] = None  # 最后一帧（用于超时返回）
        
    async def start(self):
        """启动帧捕获"""
        if self._is_running:
            logger.debug("VideoFrameProducer 已经在运行，跳过启动")
            return
            
        try:
            logger.info(f"启动 VideoFrameProducer，质量: {self.config.quality}")
            self.screencast_session = await self.page.screencast.start(
                on_frame=self._on_frame_callback,
                quality=self.config.quality
            )
            self._is_running = True
            
            # 初始化最后一帧为绿屏（避免一开始返回 None）
            self._last_frame = self._create_green_frame()
            if self._last_frame:
                logger.info("已初始化绿屏帧")
            
            logger.info("VideoFrameProducer 启动成功")
        except Exception as e:
            logger.error(f"启动 VideoFrameProducer 失败: {e}")
            raise
            
    async def stop(self):
        """停止帧捕获并清理资源"""
        if not self._is_running:
            return
            
        try:
            if self.screencast_session:
                await self.screencast_session.stop()
                logger.info("Screencast session 已停止")
        except Exception as e:
            logger.error(f"停止 Screencast session 时出错: {e}")
        finally:
            self._is_running = False
            self.screencast_session = None
            
    async def _on_frame_callback(self, frame_data: dict):
        """
        Playwright screencast 回调函数
        
        当有新的帧可用时被调用。采用丢旧保新策略：
        如果队列已满，丢弃最旧的帧以容纳新帧。
        
        Args:
            frame_data: 包含 'data' 字段的字典，{'data': <bytes>, 'format': 'jpeg'}
        """
        if not self._is_running:
            return
        
        # Playwright 返回的是字典，需要提取 'data' 字段
        if isinstance(frame_data, dict):
            jpeg_data = frame_data.get('data')
            if jpeg_data is None:
                logger.warning(f"Frame data 缺少 'data' 字段: {frame_data.keys()}")
                return
        else:
            # 兼容直接传入 bytes 的情况
            jpeg_data = frame_data
            
        # 丢旧保新策略
        if self.frame_queue.full():
            try:
                self.frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
                
        await self.frame_queue.put(jpeg_data)
        
    async def get_next_frame(self) -> Optional[av.VideoFrame]:
        """
        获取下一帧（异步）
        
        从队列中获取 JPEG 数据并在线程池中解码为 av.VideoFrame。
        如果队列为空，会一直阻塞等待新帧（不会超时）。
        这是唯一的外部访问接口，供 WebRTCMediaTrack 调用。
        
        Returns:
            av.VideoFrame: 解码后的视频帧（YUV420P 格式）
            None: 如果生产者已停止
        """
        if not self._is_running:
            return None
            
        try:
            # 从队列获取 JPEG 数据（一直阻塞等待，直到有新帧或生产者停止）
            jpeg_data = await self.frame_queue.get()
            
            # 在线程池中解码，避免阻塞事件循环
            frame = await asyncio.to_thread(self._decode_jpeg, jpeg_data)
            
            # 保存最后一帧
            self._last_frame = frame
            
            return frame
            
        except asyncio.CancelledError:
            logger.debug("get_next_frame 被取消")
            return None
        except Exception as e:
            logger.error(f"获取视频帧时出错: {e}")
            return None
            
    def _decode_jpeg(self, jpeg_data: bytes) -> av.VideoFrame:
        """
        将 JPEG 字节解码为 av.VideoFrame
        
        此方法应该在线程池中执行，因为它是 CPU 密集型的。
        
        Args:
            jpeg_data: JPEG 编码的图像数据
            
        Returns:
            av.VideoFrame: YUV420P 格式的视频帧
        """
        try:
            # JPEG → PIL Image
            image = Image.open(io.BytesIO(jpeg_data))
            
            # PIL Image → av.VideoFrame
            frame = av.VideoFrame.from_image(image)
            
            # 转换为 WebRTC 标准格式 YUV420P
            frame = frame.reformat(format="yuv420p")
            
            return frame
            
        except Exception as e:
            logger.error(f"JPEG 解码失败: {e}")
            # 返回绿屏帧作为错误恢复
            return self._create_green_frame()
    
    def _create_green_frame(self) -> Optional[av.VideoFrame]:
        """
        创建绿屏帧（用于初始化或错误恢复）
        
        Returns:
            av.VideoFrame: 640x480 的绿色帧，或 None（如果创建失败）
        """
        try:
            # 创建绿色图像
            image = Image.new('RGB', (640, 480), color='green')
            frame = av.VideoFrame.from_image(image)
            return frame.reformat(format="yuv420p")
        except Exception as e:
            logger.error(f"创建绿屏帧失败: {e}")
            return None
            
    @property
    def is_running(self) -> bool:
        """检查生产者是否正在运行"""
        return self._is_running
        
    @property
    def queue_size(self) -> int:
        """获取当前队列中的帧数"""
        return self.frame_queue.qsize()
