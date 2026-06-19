"""
Runtime 模块 - LiveService 数据模型

定义 LiveService 等服务中使用的内部数据模型（dataclass）。
"""

from dataclasses import dataclass, field
from typing import Set
import time
from app.models.runtime.control import (
    BrowserStatusEnum,
    OperationPriority,
    BrowserCleanupPolicy,
    SessionLifecycleState,
)
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    WebRTCEnabledSession,
)


@dataclass
class BrowserSessionEntry:
    """浏览器会话条目"""
    mid: int
    browser_id: int
    browser_session: WebRTCEnabledSession
    active_connections: Set[str] = field(default_factory=set)
    last_activity: int = 0
    status: BrowserStatusEnum = BrowserStatusEnum.RUNNING
    is_manual_mode: bool = False
    current_operation_priority: OperationPriority = OperationPriority.NORMAL
    automation_paused_time: int = 0
    manual_operation_start_time: int = 0
    cleanup_policy: BrowserCleanupPolicy = field(
        default_factory=BrowserCleanupPolicy)
    created_at: int = field(default_factory=lambda: int(time.time()))
    lifecycle_state: SessionLifecycleState = SessionLifecycleState.ACTIVE
    expires_at: int | None = None

    @property
    def is_expired(self) -> bool:
        """检查会话是否已过期"""
        return int(time.time()) > self.expires_at if self.expires_at else False

    @property
    def idle_duration(self) -> int:
        """获取闲置时长（秒）"""
        return int(time.time()) - self.last_activity

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

        # 基于闲置的过期时间
        if self.is_idle and self.no_active_connections:
            idle_expires = self.last_activity + policy.max_idle_time
            if calculated is None or idle_expires < calculated:
                calculated = idle_expires

        return calculated


    @property
    def browser_running(self) -> bool:
        """检查浏览器是否正在运行（委托给 browser_session）"""
        return not self.browser_session.is_closed

    @property
    def page_count(self) -> int:
        """获取页面数量（委托给 browser_session）"""
        try:
            return len(self.browser_session.browser_context.pages)
        except Exception:
            return 0


__all__ = [
    "BrowserSessionEntry",
]
