"""
Feedback 模块 - 报错 / 反馈响应模型

定义「用户提交报错 / 反馈」的 API 响应模型。

时间字段统一用 bili_common 的 ``OptionalUnixTime``：出参为**秒级 int 时间戳**（可空），
禁止 ISO 字符串（见 .codebuddy/rules/unix-timestamp.mdc）。
"""

from sqlmodel import SQLModel, Field

from bili_common.core.time_utils import OptionalUnixTime

from app.models.feedback.feedback_requests import FeedbackContext


class FeedbackItem(SQLModel):
    """单条反馈展示模型（用户侧）"""

    report_id: str = Field(description="报告唯一标识")
    category: str = Field(description="分类：bug / suggestion / other")
    title: str = Field(description="简短标题")
    description: str = Field(description="详细描述")
    context: FeedbackContext | None = Field(default=None, description="错误上下文")
    status: str = Field(description="处理状态：pending / resolved / ignored")
    created_at: OptionalUnixTime = Field(default=None, description="提交时间（秒级时间戳）")


class FeedbackSubmitResp(SQLModel):
    """提交反馈的响应模型"""

    report_id: str = Field(description="报告唯一标识")
    mid: str | None = Field(
        default=None, description="提交者 mid（服务端回填；未登录提交为 null）"
    )
    uname: str | None = Field(default=None, description="提交者昵称快照")
    created_at: OptionalUnixTime = Field(default=None, description="提交时间（秒级时间戳）")
    notified: bool = Field(
        default=False, description="是否已实时推送服主（推送为弱依赖，失败不阻塞提交）"
    )


class FeedbackListResp(SQLModel):
    """反馈列表响应模型（分页）"""

    items: list[FeedbackItem] = Field(default_factory=list, description="反馈列表")
    total: int = Field(default=0, description="总条数")
    page: int = Field(default=1, description="页码")
    per_page: int = Field(default=10, description="每页条数")


class FeedbackAdminItem(SQLModel):
    """单条反馈展示模型（管理端，全字段）"""

    id: int = Field(description="数据库主键")
    report_id: str = Field(description="报告唯一标识")
    mid: str | None = Field(
        default=None, description="提交者 mid（游客为 null，字符串形式）"
    )
    uname: str | None = Field(default=None, description="提交者昵称快照")
    contact: str | None = Field(default=None, description="联系方式")
    category: str = Field(description="分类：bug / suggestion / other")
    title: str = Field(description="简短标题")
    description: str = Field(description="详细描述")
    context: FeedbackContext | None = Field(default=None, description="错误上下文")
    status: str = Field(description="处理状态：pending / resolved / ignored")
    admin_remark: str = Field(default="", description="管理端处理备注")
    handled_by: int | None = Field(default=None, description="处理人 mid")
    handled_at: OptionalUnixTime = Field(default=None, description="处理时间（秒级时间戳）")
    created_at: OptionalUnixTime = Field(default=None, description="提交时间（秒级时间戳）")
    updated_at: OptionalUnixTime = Field(
        default=None, description="最近更新时间（秒级时间戳）"
    )


class FeedbackAdminDeleteResp(SQLModel):
    """管理端批量删除反馈的响应"""

    deleted: int = Field(default=0, description="实际删除条数")
    report_ids: list[str] = Field(
        default_factory=list, description="请求删除的报告唯一标识列表"
    )


__all__ = [
    "FeedbackItem",
    "FeedbackSubmitResp",
    "FeedbackListResp",
    "FeedbackAdminItem",
    "FeedbackAdminDeleteResp",
]
