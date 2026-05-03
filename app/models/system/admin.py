"""
System 模块 - 管理员响应模型
"""

from sqlmodel import SQLModel, Field
from typing import Dict, Any
from app.models.runtime.control import BrowserCleanupPolicy


class AdminSessionInfo(SQLModel):
    """管理员会话信息"""
    mid: int = Field(description="用户ID")
    browser_id: int = Field(description="浏览器实例ID")
    session_key: str = Field(description="会话键")
    created_at: int = Field(description="创建时间")
    last_activity: int = Field(description="最后活动时间")
    is_manual_mode: bool = Field(description="是否手动模式")
    priority: str = Field(description="优先级")
    active_connections: int = Field(description="活跃连接数")
    cleanup_policy: Dict[str, Any] = Field(description="清理策略")


class AdminAllSessionsResponse(SQLModel):
    """管理员获取所有会话响应"""
    total: int = Field(description="总会话数")
    sessions: list[AdminSessionInfo] = Field(description="会话列表")


class AdminLiveStreamInfo(SQLModel):
    """管理员直播流信息"""
    session_key: str = Field(description="会话键")
    mid: int = Field(description="用户ID")
    browser_id: int = Field(description="浏览器实例ID")
    is_active: bool = Field(description="是否活跃")
    params: Dict[str, Any] = Field(description="流参数")


class AdminWebRTCConnectionInfo(SQLModel):
    """管理员 WebRTC 连接信息"""
    connection_key: str = Field(description="连接键")
    mid: int = Field(description="用户ID")
    browser_id: int = Field(description="浏览器实例ID")
    active: bool = Field(description="是否活跃")
    ice_connection_state: str = Field(description="ICE连接状态")
    connection_state: str = Field(description="连接状态")



class BrowserSessionConfigResponse(SQLModel):
    """浏览器会话配置响应"""
    auto_cleanup: bool = Field(description="是否启用自动清理")
    max_idle_time: int = Field(description="最大闲置时间（秒）")
    max_no_heartbeat_time: int = Field(description="最大无心跳时间（秒）")
    cleanup_interval: int = Field(description="清理检查间隔（秒）")
    expiration_time: int | None = Field(None, description="会话过期时间（秒），None表示不过期")


class UpdateBrowserSessionConfigRequest(SQLModel):
    """更新浏览器会话配置请求"""
    auto_cleanup: bool | None = Field(None, description="是否启用自动清理")
    max_idle_time: int | None = Field(None, description="最大闲置时间（秒）", ge=60)
    max_no_heartbeat_time: int | None = Field(None, description="最大无心跳时间（秒）", ge=30)
    cleanup_interval: int | None = Field(None, description="清理检查间隔（秒）", ge=60)
    expiration_time: int | None = Field(None, description="会话过期时间（秒），None表示不过期", ge=300)


__all__ = [
    "AdminSessionInfo",
    "AdminAllSessionsResponse",
    "AdminLiveStreamInfo",
    "AdminWebRTCConnectionInfo",
    "BrowserSessionConfigResponse",
    "UpdateBrowserSessionConfigRequest",
]
