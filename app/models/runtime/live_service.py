"""
Runtime 模块 - LiveService 数据模型

定义 LiveService 等服务中使用的内部数据模型（dataclass）。
"""

from dataclasses import dataclass, field
from typing import Dict, Set, Any, TYPE_CHECKING
from typing_extensions import TypeGuard
import time
import asyncio
from app.services.RPA_browser.browser_session_pool.webrtc_session import (
    WebRTCEnabledSession,
)
from app.models.runtime.control import (
    BrowserStatusEnum,
    OperationPriority,
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

    # WebRTC 连接追踪（与 active_connections 分离，专门记录 WebRTC 连接）
    webrtc_connections: Set[str] = field(default_factory=set)

    # WebRTC 视频流实例追踪
    webrtc_streams: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        """检查会话是否已过期"""
        if self.expires_at:
            return int(time.time()) > self.expires_at
        return False

    @property
    def idle_duration(self) -> int:
        """获取闲置时长（秒）"""
        return int(time.time()) - self.last_activity

    @property
    def heartbeat_duration(self) -> int:
        """获取距离上次心跳的时长（秒）"""
        return int(time.time()) - self.last_heartbeat

    @property
    def has_active_clients(self) -> bool:
        """检查是否有活跃的心跳客户端"""
        return len(self.heartbeat_clients) > 0

    @property
    def active_client_count(self) -> int:
        """获取活跃客户端数量"""
        return len(self.heartbeat_clients)

    @property
    def is_idle(self) -> bool:
        """检查是否处于闲置状态"""
        return self.status == BrowserStatusEnum.IDLE

    @property
    def no_active_connections(self) -> bool:
        """检查是否没有活跃连接"""
        return len(self.active_connections) == 0

    @property
    def calculated_expires_at(self) -> int | None:
        """动态计算过期时间：基于清理策略和当前状态"""
        if self.expires_at:
            return self.expires_at

        current_time = int(time.time())
        policy = self.cleanup_policy
        calculated = None

        # 基于心跳的过期时间
        if not self.has_active_clients:
            heartbeat_expires = self.last_heartbeat + policy.max_no_heartbeat_time
            calculated = heartbeat_expires

        # 基于闲置的过期时间
        if self.is_idle and self.no_active_connections:
            idle_expires = self.last_activity + policy.max_idle_time
            if calculated is None or idle_expires < calculated:
                calculated = idle_expires

        return calculated

    @property
    def webrtc_session(self):
        """
        获取已启用 WebRTC 的会话

        Returns:
            PluginedSessionInfo 或 None（如果未启用 WebRTC）

        Example:
            >>> if entry.webrtc_session:
            ...     mgr = entry.webrtc_session.webrtc_manager
            ...     stream = mgr.streams.get(0)
        """
        if self.has_webrtc():
            return self.plugined_session
        return None

    def has_webrtc(self) -> bool:
        """
        检查会话是否支持 WebRTC

        Returns:
            bool: 如果已启用 WebRTC 则返回 True

        Example:
            >>> if entry.has_webrtc():
            ...     await entry.webrtc_session.get_webrtc_offer()
        """
        # 检查 plugined_session 是否有 _webrtc_manager 属性且不为 None
        return (
            hasattr(self.plugined_session, "_webrtc_manager")
            and self.plugined_session._webrtc_manager is not None
        )

    @property
    def browser_running(self) -> bool:
        """检查浏览器是否正在运行（委托给 plugined_session）"""
        return not self.plugined_session.is_closed

    @property
    def page_count(self) -> int:
        """获取页面数量（委托给 plugined_session）"""
        try:
            return len(self.plugined_session.browser_context.pages)
        except Exception:
            return 0


__all__ = [
    "BrowserSessionEntry",
]
