"""管理员响应模型"""
from sqlmodel import SQLModel, Field
from typing import Dict, Any


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


class AdminAllStreamsResponse(SQLModel):
    """管理员获取所有流响应"""
    live_streams_count: int = Field(description="直播流数量")
    live_streams: list[AdminLiveStreamInfo] = Field(description="直播流列表")
    webrtc_connections_count: int = Field(description="WebRTC连接数量")
    webrtc_connections: list[AdminWebRTCConnectionInfo] = Field(description="WebRTC连接列表")


class AdminAllStatsResponse(SQLModel):
    """管理员获取所有统计响应"""
    session_stats: Dict[str, Any] = Field(description="会话统计")
    live_streams_count: int = Field(description="直播流数量")
    webrtc_connections_count: int = Field(description="WebRTC连接数量")
    timestamp: int = Field(description="时间戳")
