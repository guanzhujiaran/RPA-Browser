"""
WebRTCMediaTrack - WebRTC 视频媒体轨道

实现 aiortc 的 VideoStreamTrack 接口，将 VideoFrameProducer 产生的帧
提供给 WebRTC PeerConnection。
"""

import time

from aiortc import VideoStreamTrack
from aiortc.mediastreams import (
    VIDEO_CLOCK_RATE,
    VIDEO_TIME_BASE,
    MediaStreamError,
)
from loguru import logger

from .video_frame_producer import VideoFrameProducer


class WebRTCMediaTrack(VideoStreamTrack):
    """
    WebRTC 视频媒体轨道

    继承自 aiortc.VideoStreamTrack，从 VideoFrameProducer 获取视频帧
    并通过 WebRTC 连接发送给客户端。

    时间戳策略：**以墙钟时间为准**，不使用父类 `next_timestamp()`。原因是
    aiortc 的 `VIDEO_PTIME` 硬编码为 1/30 —— 它按 30fps 递增 PTS 并 sleep，
    而生产者已按 `max_fps` / 降级帧率做了真实节流（降级可低至 5fps），
    两者叠加会让时间轴与真实出帧节奏脱钩（接收端抖动/回放异常）。
    """

    def __init__(self, producer: VideoFrameProducer):
        """
        初始化媒体轨道

        Args:
            producer: VideoFrameProducer 实例，提供视频帧
        """
        super().__init__()
        self.producer = producer
        self._start_time: float | None = None
        self._last_pts: int = 0
        logger.info("WebRTCMediaTrack 已初始化")

    async def recv(self):
        """
        接收下一帧

        由 aiortc 内部调用，当需要发送新帧时触发。

        Returns:
            av.VideoFrame: 带有时间戳的视频帧

        Raises:
            MediaStreamError: 生产者已停止（aiortc 据此静默结束轨道）
        """
        # 从生产者获取下一帧（生产者内部已按目标帧率完成节流）
        frame = await self.producer.get_next_frame()

        if frame is None:
            # 生产者已停止。必须用 MediaStreamError：aiortc 的 sender 只对它
            # 静默收尾，而 StopIteration 会被 asyncio 包装成 RuntimeError 打 warning。
            logger.info("VideoFrameProducer 已停止，轨道结束")
            raise MediaStreamError("VideoFrameProducer 已停止")

        if self.readyState != "live":
            raise MediaStreamError("轨道已不再活跃")

        # 墙钟时间 → 90kHz PTS，并保证严格单调递增
        now = time.monotonic()
        if self._start_time is None:
            self._start_time = now
        pts = int((now - self._start_time) * VIDEO_CLOCK_RATE)
        if pts <= self._last_pts:
            pts = self._last_pts + 1
        self._last_pts = pts

        frame.pts = pts
        frame.time_base = VIDEO_TIME_BASE
        return frame
