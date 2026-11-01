"""工作流运行通知的「通道 × 时机」矩阵

见 ``docs/通知动作与运行告警计划书.md`` §E：通知开关从两个「通道混合」布尔
（``notify_on_start`` / ``notify_on_end``）拆成 2×2 四象限 —— 行 = 通道，
列 = 时机（开始 / 结束，结束含成功、失败、停止）。

解析集中在本模块，执行侧（``services/execution/workflow/notify.py``）只消费
``channels()`` 返回的通道集合，避免多处各自判断开关口径。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.database.workflow.models import UserWorkflow


class NotifyChannel(StrEnum):
    """运行通知投递通道"""

    INBOX = "inbox"
    """站内系统通知：be-message ``msg_notify``，无需用户配置"""

    PUSH = "push"
    """站外推送：PushMe / PushPlus / webhook 等，需用户配置 ``NotificationConfig``"""


@dataclass(frozen=True, slots=True)
class NotifyChannelFlags:
    """通知开关矩阵：行 = 通道（站内 / 站外），列 = 时机（开始 / 结束）"""

    inbox_on_start: bool = False
    inbox_on_end: bool = True
    push_on_start: bool = False
    push_on_end: bool = True

    def channels(self, *, is_start: bool) -> frozenset[NotifyChannel]:
        """本次时机应投递的通道集合；空集表示「一个都不发」"""
        pairs: tuple[tuple[NotifyChannel, bool], ...] = (
            (
                NotifyChannel.INBOX,
                self.inbox_on_start if is_start else self.inbox_on_end,
            ),
            (
                NotifyChannel.PUSH,
                self.push_on_start if is_start else self.push_on_end,
            ),
        )
        return frozenset(channel for channel, enabled in pairs if enabled)

    @property
    def legacy_on_start(self) -> bool:
        """旧字段（通道混合）派生值：任一通道要发即为真"""
        return self.inbox_on_start or self.push_on_start

    @property
    def legacy_on_end(self) -> bool:
        """旧字段（通道混合）派生值：任一通道要发即为真"""
        return self.inbox_on_end or self.push_on_end

    @classmethod
    def from_model(cls, workflow: UserWorkflow) -> NotifyChannelFlags:
        """从工作流模型读取矩阵（旧字段不作为回退：迁移已把旧值展开到两列）"""
        return cls(
            inbox_on_start=bool(workflow.notify_inbox_on_start),
            inbox_on_end=bool(workflow.notify_inbox_on_end),
            push_on_start=bool(workflow.notify_push_on_start),
            push_on_end=bool(workflow.notify_push_on_end),
        )

    @classmethod
    def from_legacy(cls, *, on_start: bool, on_end: bool) -> NotifyChannelFlags:
        """旧字段（通道混合开关）按「双通道」展开"""
        return cls(
            inbox_on_start=on_start,
            inbox_on_end=on_end,
            push_on_start=on_start,
            push_on_end=on_end,
        )


__all__ = ["NotifyChannel", "NotifyChannelFlags"]
