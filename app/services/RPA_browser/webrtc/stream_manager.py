"""
WebRTCStreamManager - WebRTC 流管理器

管理单个浏览器会话（BrowserContext）下的所有 WebRTC 视频流。
负责流的创建、查询、关闭以及闲置超时的自动清理。
"""

import asyncio
import time
from typing import Dict, Optional
from loguru import logger

from app.config import settings
from app.models.runtime.webrtc_models import WebRTCSessionConfig
from .stream_session import WebRTCStreamSession


class WebRTCStreamManager:
    """
    WebRTC 流管理器
    
    管理一个浏览器会话中的所有视频流，每个页面对应一个独立的流。
    提供统一的 API 来操作这些流，并定期清理闲置的流以释放资源。
    """
    
    def __init__(self, session):
        """
        初始化流管理器
        
        Args:
            session: WebRTCEnabledSession 实例（父会话对象）
        """
        self.session = session
        self.streams: Dict[int, WebRTCStreamSession] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._is_cleanup_running = False
        
    async def start_stream(self, page_index: int) -> WebRTCStreamSession:
        """
        启动指定页面的 WebRTC 视频流
        
        每次调用都会创建全新的流实例，不依赖缓存机制。
        
        Args:
            page_index: 页面索引（从 0 开始）
            
        Returns:
            WebRTCStreamSession: 视频流会话实例
            
        Raises:
            IndexError: 如果页面索引超出范围
            Exception: 如果启动流失败
        """
        # 检查页面是否存在
        pages = await self.session.get_all_pages()
        if page_index >= len(pages):
            raise IndexError(
                f"Page index {page_index} out of range. "
                f"Available pages: {len(pages)}"
            )
        
        # 获取页面对象
        page = pages[page_index]
        
        # 如果该 page_index 已有活跃的流，先关闭它
        if page_index in self.streams:
            logger.info(f"检测到 page_index={page_index} 已有活跃流，先关闭旧流")
            old_stream = self.streams[page_index]
            try:
                await old_stream.close()
            except Exception as e:
                logger.warning(f"关闭旧流时出错（继续创建新流）: {e}")
            finally:
                del self.streams[page_index]
        
        # 生成 stream_key（使用 page_index 而非 page_id）
        mid = self.session.playwright_instance.mid
        browser_id = self.session.playwright_instance.browser_id
        stream_key = f"{mid}:{browser_id}:page_{page_index}"
        
        # 创建配置
        config = WebRTCSessionConfig(
            quality=80,
            idle_timeout=settings.browser_webrtc_idle_timeout
        )
        
        # 创建并启动流
        stream = WebRTCStreamSession(stream_key, page, config, page_index)
        await stream.start()
        
        # 存储流（使用 page_index 作为 key）
        self.streams[page_index] = stream
        
        logger.info(f"WebRTC 流已创建: {stream_key} (page_index={page_index})")
        
        # 启动清理任务（如果尚未启动）
        if not self._is_cleanup_running:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            self._is_cleanup_running = True
            
        return stream
        
    async def get_stream(self, page) -> Optional[WebRTCStreamSession]:
        """
        获取指定页面的视频流
        
        Args:
            page: Playwright Page 对象
            
        Returns:
            WebRTCStreamSession 或 None（如果不存在）
        """
        page_id = page._webrtc_page_id
        return self.streams.get(page_id)
        
    async def close_stream(self, page_index: int):
        """
        关闭指定页面索引的视频流
        
        Args:
            page_index: 页面索引（从 0 开始）
        """
        if page_index not in self.streams:
            logger.debug(f"尝试关闭不存在的流: page_index={page_index}")
            return
        
        stream = self.streams[page_index]
        try:
            await stream.close()
            logger.info(f"WebRTC 流已关闭: page_index={page_index}")
        except Exception as e:
            logger.error(f"关闭 WebRTC 流时出错 page_index={page_index}: {e}")
        finally:
            # 从字典中移除
            if page_index in self.streams:
                del self.streams[page_index]
            
    async def close_all_streams(self):
        """关闭所有视频流"""
        if not self.streams:
            return
            
        logger.info(f"关闭所有 WebRTC 流，共 {len(self.streams)} 个")
        
        # 并行关闭所有流
        tasks = []
        for page_index in list(self.streams.keys()):
            stream = self.streams[page_index]
            tasks.append(stream.close())
            
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        # 清空字典
        self.streams.clear()
            
        logger.info("所有 WebRTC 流已关闭")
        
    async def _cleanup_loop(self):
        """
        定期清理闲置的视频流
        
        每分钟检查一次，关闭超过闲置超时的流。
        """
        try:
            while self._is_cleanup_running:
                await asyncio.sleep(60)  # 每分钟检查一次
                
                current_time = time.time()
                streams_to_close = []
                
                # 检查每个流的闲置时间
                for page_index, stream in self.streams.items():
                    idle_time = stream.webrtc_state.idle_duration
                    
                    if idle_time > stream.config.idle_timeout:
                        logger.warning(
                            f"WebRTC 流因闲置超时而关闭: "
                            f"page_index={page_index}, "
                            f"idle_time={idle_time:.0f}s, "
                            f"timeout={stream.config.idle_timeout}s"
                        )
                        streams_to_close.append(page_index)
                        
                # 关闭超时的流
                for page_index in streams_to_close:
                    stream = self.streams[page_index]
                    await stream.close()
                    del self.streams[page_index]
                    
        except asyncio.CancelledError:
            logger.info("WebRTC 清理任务被取消")
        except Exception as e:
            logger.error(f"WebRTC 清理循环出错: {e}")
        finally:
            self._is_cleanup_running = False
            
    async def stop_cleanup(self):
        """停止清理任务"""
        self._is_cleanup_running = False
        
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
                
    @property
    def active_stream_count(self) -> int:
        """获取活跃流的数量"""
        return sum(1 for s in self.streams.values() if s.is_active)
        
    @property
    def total_stream_count(self) -> int:
        """获取总流数量"""
        return len(self.streams)
        
    def get_stream_keys(self) -> list[str]:
        """获取所有流的 stream_key 列表"""
        return [s.stream_key for s in self.streams.values()]
