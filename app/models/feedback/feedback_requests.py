"""
Feedback 模块 - 报错 / 反馈请求模型

定义「用户提交报错 / 反馈」的 API 请求模型。字段扁平、便于 LLM 通过 MCP 填写，
多个可选上下文字段在服务层合并进 `context` JSON。
"""

from typing import Any

from sqlmodel import SQLModel, Field
from bili_common.models import StrEnumAutoDoc


class FeedbackCategory(StrEnumAutoDoc):
    """反馈分类"""

    bug = "bug"  # 缺陷 / 报错
    suggestion = "suggestion"  # 改进建议
    other = "other"  # 其他


class FeedbackSubmitRequest(SQLModel):
    """提交报错 / 反馈的请求模型"""

    category: FeedbackCategory = Field(
        default=FeedbackCategory.bug,
        description="分类：bug(缺陷/报错) / suggestion(改进建议) / other(其他)",
    )
    title: str = Field(..., min_length=1, max_length=256, description="简短标题")
    description: str = Field(
        ..., min_length=1, description="详细描述：错误现象、期望行为、复现步骤等"
    )

    # 可选上下文：服务层合并进 context JSON，便于服主与 LLM 定位问题
    source: str | None = Field(
        default=None, max_length=32, description="来源标记：mcp / web"
    )
    action_id: int | str | None = Field(default=None, description="相关动作 id")
    workflow_id: int | str | None = Field(default=None, description="相关工作流 id")
    run_id: str | None = Field(default=None, max_length=64, description="相关运行记录 id")
    browser_id: int | str | None = Field(default=None, description="相关浏览器 id")
    error_message: str | None = Field(default=None, description="错误摘要")
    error_detail: str | None = Field(
        default=None, description="错误详情 / 堆栈 / 失败步骤"
    )
    page_url: str | None = Field(default=None, description="出错时页面 URL")
    extra: dict[str, Any] | None = Field(
        default=None, description="其他键值对（补充上下文）"
    )


class FeedbackListRequest(SQLModel):
    """查询当前用户反馈列表的请求模型（分页）"""

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    per_page: int = Field(default=10, ge=1, le=100, description="每页条数")


__all__ = [
    "FeedbackCategory",
    "FeedbackSubmitRequest",
    "FeedbackListRequest",
]
