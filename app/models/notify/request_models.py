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


__all__ = [
    "NotifyConfigReadRequest",
    "BrowserEffectiveNotifyRequest",
]
