"""
WebRTC 视频流服务模块

提供浏览器页面到客户端的 WebRTC 单向视频流传输功能。

多观看者并发直播（见 docs/rpa-多观看者并发直播计划书.md）：

- `PageFrameSource`：**帧源**，一个 page 一份（唯一 screencast），向多个观看者广播帧
- `ViewerStream`：**观看者**，一条 PeerConnection 一份，档位 / 暂停独立
- `WebRTCStreamManager`：按会话聚合两者
"""

from .video_frame_producer import FrameSlot, VideoFrameProducer, clone_video_frame
from .frame_source import PageFrameSource
from .viewer_stream import ViewerMediaTrack, ViewerStream
from .stream_manager import WebRTCStreamManager

__all__ = [
    "VideoFrameProducer",
    "FrameSlot",
    "clone_video_frame",
    "PageFrameSource",
    "ViewerMediaTrack",
    "ViewerStream",
    "WebRTCStreamManager",
]
