"""
System 模块 - 系统相关模型

包含管理员和权限相关模型。
"""

from app.models.system.admin import (
    AdminSessionInfo,
    AdminAllSessionsResponse,
    AdminLiveStreamInfo,
    AdminWebRTCConnectionInfo,
    AdminAllStreamsResponse,
    AdminAllStatsResponse,
)
from app.models.system.permission import (
    PermissionLevelConfig,
    PermissionConfigList,
    PermissionConfigData,
)

__all__ = [
    # Admin
    "AdminSessionInfo",
    "AdminAllSessionsResponse",
    "AdminLiveStreamInfo",
    "AdminWebRTCConnectionInfo",
    "AdminAllStreamsResponse",
    "AdminAllStatsResponse",
    # Permission
    "PermissionLevelConfig",
    "PermissionConfigList",
    "PermissionConfigData",
]
