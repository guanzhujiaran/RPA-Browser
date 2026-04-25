"""
Notify 模块 - 通知请求模型

定义通知相关的API请求模型。
"""

from sqlmodel import SQLModel, Field


class NotifyConfigReadRequest(SQLModel):
    """读取通知配置的请求模型"""
    browser_id: str | None = Field(None, description="浏览器实例ID")


class BrowserEffectiveNotifyRequest(SQLModel):
    """获取特定浏览器有效通知配置的请求模型"""
    browser_id: str = Field(..., description="浏览器实例ID")


class TestNotificationRequest(SQLModel):
    """测试推送通知的请求模型"""
    title: str = Field(default="测试通知", description="通知标题")
    content: str = Field(default="这是一条测试通知消息", description="通知内容")
    browser_id: str | None = Field(None, description="浏览器实例ID")


class TestNotificationResponse(SQLModel):
    """测试推送通知的响应模型"""
    success: bool = Field(..., description="测试是否成功")
    message: str = Field(..., description="测试结果消息")
    config_found: bool = Field(..., description="是否找到通知配置")
    browser_id: str | None = Field(None, description="使用的浏览器实例ID")
    config_source: str | None = Field(None, description="配置来源：'browser' 或 'global'")
    sent_channels: list[str] = Field(default=[], description="成功发送的推送渠道列表")


__all__ = [
    "NotifyConfigReadRequest",
    "BrowserEffectiveNotifyRequest",
    "TestNotificationRequest",
    "TestNotificationResponse",
]
