"""
Notify Model - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.notify 中的模型。
"""

from app.models.notify.models import (
    NotificationConfigBase,
    NotificationConfig,
    NotificationConfigCreate,
    NotificationConfigUpdate,
)
from app.models.notify.request_models import (
    NotifyConfigReadRequest,
    BrowserEffectiveNotifyRequest,
    TestNotificationRequest,
    TestNotificationResponse,
)
from app.models.notify.response_models import (
    NotificationConfigUpsertResp,
    NotificationConfigDeleteResp,
    NotificationConfigEffectiveResp,
)

__all__ = [
    "NotificationConfigBase",
    "NotificationConfig",
    "NotificationConfigCreate",
    "NotificationConfigUpdate",
    "NotifyConfigReadRequest",
    "BrowserEffectiveNotifyRequest",
    "TestNotificationRequest",
    "TestNotificationResponse",
    "NotificationConfigUpsertResp",
    "NotificationConfigDeleteResp",
    "NotificationConfigEffectiveResp",
]
