"""
Notify 模块 - 通知相关模型

包含通知配置、通知请求/响应等模型。
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
    # Models
    "NotificationConfigBase",
    "NotificationConfig",
    "NotificationConfigCreate",
    "NotificationConfigUpdate",
    # Request Models
    "NotifyConfigReadRequest",
    "BrowserEffectiveNotifyRequest",
    "TestNotificationRequest",
    "TestNotificationResponse",
    # Response Models
    "NotificationConfigUpsertResp",
    "NotificationConfigDeleteResp",
    "NotificationConfigEffectiveResp",
]
