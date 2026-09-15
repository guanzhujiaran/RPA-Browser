"""
Runtime 模块 - LiveService 数据模型

定义 LiveService 等服务中使用的内部数据模型（dataclass）。
"""

from dataclasses import dataclass, field
from typing import Set
import time
from app.config import settings
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
    # 自动化任务占用计数（>0 时禁止一切降级/关闭），见 §5.15
    pin_count: int = 0
    # 闲置超时后进入宽限期的时间戳（用于「倒计时关实例」）
    terminate_scheduled_at: int | None = None

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
        """检查是否处于闲置状态（挂起时 status 置为 IDLE）"""
        return self.status == BrowserStatusEnum.IDLE

    @property
    def is_pinned(self) -> bool:
        """是否被自动化任务占用（占用期间禁止降级/关闭）"""
        return self.pin_count > 0

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

        # 闲置关实例：一旦进入宽限期，以「宽限截止」作为临期时间
        if self.terminate_scheduled_at:
            calculated = self.terminate_scheduled_at + settings.browser_session_terminate_grace
        # 未进入宽限时，给出预计的关实例时间
        elif self.is_idle and self.no_active_connections:
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
