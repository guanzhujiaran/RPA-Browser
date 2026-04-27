"""
Runtime 模块 - LiveService 数据模型

定义 LiveService、VideoStreamService 等服务中使用的内部数据模型（dataclass）。
"""

from dataclasses import dataclass, field
from typing import Dict, Set
import time
import asyncio

from app.models.runtime.control import (
    BrowserStatusEnum,
    OperationPriority,
    VideoStreamParams,
    BrowserCleanupPolicy,
    SessionLifecycleState,
)
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    PluginedSessionInfo,
)


@dataclass
class BrowserSessionEntry:
    """浏览器会话条目"""

    mid: int
    browser_id: int
    plugined_session: PluginedSessionInfo
    active_connections: Set[str] = field(default_factory=set)
    last_activity: int = 0
    last_heartbeat: int = 0
    status: BrowserStatusEnum = BrowserStatusEnum.RUNNING
    is_manual_mode: bool = False
    current_operation_priority: OperationPriority = OperationPriority.NORMAL
    automation_paused_time: int = 0
    manual_operation_start_time: int = 0
    heartbeat_clients: Dict[str, int] = field(default_factory=dict)
    cleanup_policy: BrowserCleanupPolicy = field(default_factory=BrowserCleanupPolicy)
    created_at: int = field(default_factory=lambda: int(time.time()))
    lifecycle_state: SessionLifecycleState = SessionLifecycleState.ACTIVE
    expires_at: int | None = None


@dataclass
class VideoStreamInfo:
    """视频流信息"""

    mid: int
    browser_id: int
    session: PluginedSessionInfo
    params: VideoStreamParams
    active: bool = True
    last_frame: bytes | None = None
    last_frame_time: float = 0.0


@dataclass
class LiveStreamingEntry:
    """直播流条目"""

    mid: int
    browser_id: int
    start_time: int
    last_heartbeat: int
    is_active: bool = True
    stream_params: VideoStreamParams | None = None
    cleanup_scheduled: bool = False



__all__ = [
    "BrowserSessionEntry",
    "VideoStreamInfo",
    "LiveStreamingEntry",
]
