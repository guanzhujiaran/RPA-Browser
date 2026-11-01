"""
Feedback 模块 - 报错 / 反馈响应模型

定义「用户提交报错 / 反馈」的 API 响应模型。
"""

from datetime import datetime
from typing import Any

from sqlmodel import SQLModel, Field


class FeedbackItem(SQLModel):
    """单条反馈展示模型"""

    report_id: str = Field(description="报告唯一标识")
    category: str = Field(description="分类：bug / suggestion / other")
    title: str = Field(description="简短标题")
    description: str = Field(description="详细描述")
    context: dict[str, Any] | None = Field(default=None, description="错误上下文")
    status: str = Field(description="处理状态：pending / resolved / ignored")
    created_at: datetime | None = Field(default=None, description="提交时间")


class FeedbackSubmitResp(SQLModel):
    """提交反馈的响应模型"""

    report_id: str = Field(description="报告唯一标识")
    mid: str = Field(description="提交者 mid（服务端回填）")
    created_at: datetime | None = Field(default=None, description="提交时间")
    notified: bool = Field(
        default=False, description="是否已实时推送服主（推送为弱依赖，失败不阻塞提交）"
    )


class FeedbackListResp(SQLModel):
    """反馈列表响应模型（分页）"""

    items: list[FeedbackItem] = Field(default_factory=list, description="反馈列表")
    total: int = Field(default=0, description="总条数")
    page: int = Field(default=1, description="页码")
    per_page: int = Field(default=10, description="每页条数")


__all__ = [
    "FeedbackItem",
    "FeedbackSubmitResp",
    "FeedbackListResp",
]
