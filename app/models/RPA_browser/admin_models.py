"""
Admin Models - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.system.admin 中的模型。
"""

from app.models.system.admin import (
    AdminSessionInfo,
    AdminAllSessionsResponse,
    AdminLiveStreamInfo,
    AdminWebRTCConnectionInfo,
    AdminAllStreamsResponse,
    AdminAllStatsResponse,
)

__all__ = [
    "AdminSessionInfo",
    "AdminAllSessionsResponse",
    "AdminLiveStreamInfo",
    "AdminWebRTCConnectionInfo",
    "AdminAllStreamsResponse",
    "AdminAllStatsResponse",
]
