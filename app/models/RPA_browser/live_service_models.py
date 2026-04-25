"""
Live Service Models - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.runtime.live_service 中的模型。
"""

from app.models.runtime.live_service import (
    BrowserSessionEntry,
    VideoStreamInfo,
    LiveStreamingEntry,
    LiveServiceState,
)

__all__ = [
    "BrowserSessionEntry",
    "VideoStreamInfo",
    "LiveStreamingEntry",
    "LiveServiceState",
]
