"""
视频流服务 - 处理视频流相关操作

此模块提供 MJPEG 视频流生成和管理功能。
"""
import asyncio
import io
import time
from typing import Dict

from playwright.async_api import Page
from loguru import logger

from app.models.runtime.control import VideoStreamParams, VideoStreamStatusData
from app.models.runtime.live_service import VideoStreamInfo
from app.models.exceptions.base_exception import GetBrowserSessionFailedException


class VideoStreamService:
    """视频流服务类"""

    # 维护视频流状态 - 类变量
    video_streams: Dict[str, VideoStreamInfo] = {}  # key: f"{mid}_{browser_id}"
    # 视频流自动管理标志
    auto_streams: Dict[str, bool] = (
        {}
    )  # key: f"{mid}_{browser_id}" 表示是否由WebSocket自动管理

    @staticmethod
    def _get_stream_key(mid: int, browser_id: int) -> str:
        """获取流键"""
        return f"{mid}_{browser_id}"

    @staticmethod
    async def generate_video_stream(
        mid: int, browser_id: int, params: VideoStreamParams, plugined_session
    ) -> io.BytesIO:
        """生成视频流 - 使用MJPEG格式

        Args:
            mid: 用户ID
            browser_id: 浏览器ID
            params: 视频流参数
            plugined_session: 插件化会话

        Returns:
            io.BytesIO: 视频流缓冲区
        """
        stream_key = VideoStreamService._get_stream_key(mid, browser_id)

        # 创建视频流缓冲区
        stream_buffer = io.BytesIO()

        # 设置视频流状态
        VideoStreamService.video_streams[stream_key] = VideoStreamInfo(
            mid=mid,
            browser_id=browser_id,
            session=plugined_session,
            params=params,
            active=True,
            last_frame=None,
            last_frame_time=time.time(),
        )

        return stream_buffer

    @staticmethod
    async def generate_mjpeg_frame(
        page: Page, quality: int = 80, width: int = None, height: int = None
    ) -> bytes:
        """生成MJPEG帧

        Args:
            page: Playwright 页面对象
            quality: JPEG 质量 (1-100)
            width: 目标宽度
            height: 目标高度

        Returns:
            bytes: MJPEG 帧数据
        """
        try:
            # 截图并转换为JPEG
            screenshot_bytes = await page.screenshot(
                type="jpeg", quality=quality, full_page=False
            )

            # 如果有尺寸要求，使用PIL进行缩放
            if width or height:
                from PIL import Image

                image = Image.open(io.BytesIO(screenshot_bytes))

                # 计算新的尺寸
                if width and height:
                    new_size = (width, height)
                elif width:
                    ratio = width / image.width
                    new_size = (width, int(image.height * ratio))
                else:
                    ratio = height / image.height
                    new_size = (int(image.width * ratio), height)

                image = image.resize(new_size, Image.Resampling.LANCZOS)

                # 重新编码为JPEG
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality)
                screenshot_bytes = output.getvalue()

            # 构建MJPEG帧
            frame_header = f"Content-Type: image/jpeg\r\nContent-Length: {len(screenshot_bytes)}\r\n\r\n"
            frame_data = frame_header.encode() + screenshot_bytes

            return frame_data

        except Exception as e:
            # 生成错误帧
            error_frame = f"Content-Type: text/plain\r\nContent-Length: {len(str(e))}\r\n\r\n{str(e)}"
            return error_frame.encode()

    @staticmethod
    async def start_video_stream(
        mid: int, browser_id: int, params: VideoStreamParams, auto_managed: bool = False
    ) -> None:
        """启动视频流

        Args:
            mid: 用户ID
            browser_id: 浏览器ID
            params: 视频流参数
            auto_managed: 是否为自动管理模式
        """
        stream_key = VideoStreamService._get_stream_key(mid, browser_id)

        # 如果视频流未初始化，先初始化
        if stream_key not in VideoStreamService.video_streams:
            # 需要外部提供 session，这里使用占位符
            # 实际使用时应在调用前获取 session
            raise GetBrowserSessionFailedException(
                error="视频流未初始化，请先调用 generate_video_stream"
            )

        stream_info = VideoStreamService.video_streams[stream_key]
        stream_info.active = True

        # 设置自动管理标志
        VideoStreamService.auto_streams[stream_key] = auto_managed

        # 获取当前页面
        try:
            page = await stream_info.session.get_current_page()

            while stream_info.active:
                # 检查是否应该停止（自动管理模式）
                # 注意：这里需要从 LiveService 获取 live_streams 状态
                # 由于模块解耦，这里需要通过参数传入或回调方式处理
                # 简化处理：仅依赖 active 标志

                # 生成帧
                frame_data = await VideoStreamService.generate_mjpeg_frame(
                    page,
                    quality=params.quality,
                    width=params.width,
                    height=params.height,
                )

                # 更新帧数据
                stream_info.last_frame = frame_data
                stream_info.last_frame_time = time.time()

                # 等待下一帧
                await asyncio.sleep(1.0 / params.fps)

        except Exception as e:
            stream_info.active = False
            raise e

    @staticmethod
    async def stop_video_stream(mid: int, browser_id: int, force: bool = False) -> None:
        """停止视频流

        Args:
            mid: 用户ID
            browser_id: 浏览器ID
            force: 是否强制停止
        """
        stream_key = VideoStreamService._get_stream_key(mid, browser_id)

        if stream_key in VideoStreamService.video_streams:
            # 检查是否自动管理，如果不是自动管理的流或强制停止，则停止
            if force or not VideoStreamService.auto_streams.get(stream_key, False):
                VideoStreamService.video_streams[stream_key].active = False
                # 延迟清理，确保所有异步操作完成
                await asyncio.sleep(1.0)
                if stream_key in VideoStreamService.video_streams:
                    del VideoStreamService.video_streams[stream_key]

                # 清理自动管理标志
                if stream_key in VideoStreamService.auto_streams:
                    del VideoStreamService.auto_streams[stream_key]

    @staticmethod
    def get_video_stream_status(
        mid: int, browser_id: int
    ) -> VideoStreamStatusData | None:
        """获取视频流状态

        Args:
            mid: 用户ID
            browser_id: 浏览器ID

        Returns:
            Optional[VideoStreamStatusData]: 视频流状态数据
        """
        stream_key = VideoStreamService._get_stream_key(mid, browser_id)

        if stream_key in VideoStreamService.video_streams:
            stream_info = VideoStreamService.video_streams[stream_key]
            return VideoStreamStatusData(
                mid=stream_info.mid,
                browser_id=stream_info.browser_id,
                active=stream_info.active,
                last_frame_time=stream_info.last_frame_time,
                params=(
                    stream_info.params.dict()
                    if hasattr(stream_info.params, "dict")
                    else {}
                ),
            )
        return None

    @staticmethod
    async def get_latest_frame(mid: int, browser_id: int) -> bytes | None:
        """获取最新帧

        Args:
            mid: 用户ID
            browser_id: 浏览器ID

        Returns:
            Optional[bytes]: 最新帧数据
        """
        try:
            stream_key = VideoStreamService._get_stream_key(mid, browser_id)

            if stream_key in VideoStreamService.video_streams:
                stream_info = VideoStreamService.video_streams[stream_key]
                # 检查流是否仍然活跃
                if stream_info.active:
                    return stream_info.last_frame
                else:
                    # 流已不活跃，清理资源
                    del VideoStreamService.video_streams[stream_key]
                    if stream_key in VideoStreamService.auto_streams:
                        del VideoStreamService.auto_streams[stream_key]
                    return None
            return None
        except Exception:
            return None

    @staticmethod
    def is_stream_active(mid: int, browser_id: int) -> bool:
        """检查流是否活跃

        Args:
            mid: 用户ID
            browser_id: 浏览器ID

        Returns:
            bool: 是否活跃
        """
        stream_key = VideoStreamService._get_stream_key(mid, browser_id)
        return stream_key in VideoStreamService.video_streams and VideoStreamService.video_streams[stream_key].active
