"""
Feedback 模块 - 报错 / 反馈请求模型

定义「用户提交报错 / 反馈」的 API 请求模型。字段扁平、便于 LLM 通过 MCP 填写，
多个可选上下文字段在服务层合并进 `context` JSON。

反馈允许**未登录提交**：提交者身份由可选登录依赖决定，服务端回填 mid / uname，
未登录用户可自行填写 `contact` 便于服主回访。
"""

from pydantic import field_validator
from sqlmodel import SQLModel, Field
from bili_common.models import StrEnumAutoDoc

from app.models.base.base_sqlmodel import BasePaginationReq


def _validate_mid_str(value: str | None) -> str | None:
    """管理端 mid 入参统一为数字字符串（前端不传超长 int），服务端再转 int。"""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError("mid 必须为数字字符串")
    return text


class FeedbackCategory(StrEnumAutoDoc):
    """反馈分类"""

    bug = "bug"  # 缺陷 / 报错
    suggestion = "suggestion"  # 改进建议
    other = "other"  # 其他


class FeedbackStatus(StrEnumAutoDoc):
    """反馈处理状态（管理端维护）"""

    pending = "pending"  # 待处理
    resolved = "resolved"  # 已处理
    ignored = "ignored"  # 已忽略


class FeedbackContext(SQLModel):
    """报错上下文（落库时序列化为 JSON 存 ``context`` 文本列）"""

    source: str | None = Field(default=None, description="来源标记：mcp / web")
    action_id: int | str | None = Field(default=None, description="相关动作 id")
    workflow_id: int | str | None = Field(default=None, description="相关工作流 id")
    run_id: str | None = Field(default=None, description="相关运行记录 id")
    browser_id: int | str | None = Field(default=None, description="相关浏览器 id")
    error_message: str | None = Field(default=None, description="错误摘要")
    error_detail: str | None = Field(default=None, description="错误详情 / 堆栈")
    page_url: str | None = Field(default=None, description="出错时页面 URL")
    extra: str | None = Field(default=None, description="其他补充信息（自由文本 / JSON）")


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

    # 未登录用户留联系方式，便于服主回访（登录用户可选填）
    contact: str | None = Field(
        default=None,
        max_length=256,
        description="联系方式（邮箱 / QQ 等）：未登录提交时建议填写，便于服主回访",
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
    extra: str | None = Field(
        default=None, description="其他补充信息（自由文本 / JSON 字符串）"
    )


class FeedbackListRequest(SQLModel):
    """查询当前用户反馈列表的请求模型（分页）"""

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    per_page: int = Field(default=10, ge=1, le=100, description="每页条数")


class FeedbackAdminListRequest(BasePaginationReq):
    """管理端反馈列表请求（全量，不分提交者）"""

    mid: str | None = Field(default=None, description="按提交者 mid 精确筛选（数字字符串）")
    status: FeedbackStatus | None = Field(default=None, description="按处理状态筛选")
    category: FeedbackCategory | None = Field(default=None, description="按分类筛选")
    keyword: str | None = Field(
        default=None, description="关键词：模糊匹配标题 / 描述 / 联系方式"
    )
    uname: str | None = Field(default=None, description="按提交者昵称模糊筛选")

    @field_validator("mid")
    @classmethod
    def _valid_mid(cls, value: str | None) -> str | None:
        return _validate_mid_str(value)


class FeedbackAdminCreateRequest(SQLModel):
    """管理端手工补录一条反馈"""

    category: FeedbackCategory = Field(
        default=FeedbackCategory.bug, description="分类"
    )
    title: str = Field(..., min_length=1, max_length=256, description="简短标题")
    description: str = Field(..., min_length=1, description="详细描述")
    mid: str | None = Field(
        default=None, description="提交者 mid（游客留空，数字字符串）"
    )
    uname: str | None = Field(default=None, max_length=128, description="提交者昵称")
    contact: str | None = Field(default=None, max_length=256, description="联系方式")
    status: FeedbackStatus = Field(
        default=FeedbackStatus.pending, description="处理状态"
    )
    admin_remark: str = Field(default="", description="处理备注")
    context: FeedbackContext | None = Field(default=None, description="报错上下文")

    @field_validator("mid")
    @classmethod
    def _valid_mid(cls, value: str | None) -> str | None:
        return _validate_mid_str(value)


class FeedbackAdminUpdateRequest(SQLModel):
    """管理端更新一条反馈（按 report_id 定位，未传字段保持不变）"""

    report_id: str = Field(..., description="报告唯一标识")
    category: FeedbackCategory | None = Field(default=None, description="分类")
    title: str | None = Field(
        default=None, min_length=1, max_length=256, description="简短标题"
    )
    description: str | None = Field(
        default=None, min_length=1, description="详细描述"
    )
    mid: str | None = Field(
        default=None, description="提交者 mid（传 null 表示游客，数字字符串）"
    )
    uname: str | None = Field(default=None, max_length=128, description="提交者昵称")
    contact: str | None = Field(default=None, max_length=256, description="联系方式")
    status: FeedbackStatus | None = Field(default=None, description="处理状态")
    admin_remark: str | None = Field(default=None, description="处理备注")
    context: FeedbackContext | None = Field(
        default=None, description="报错上下文（整体覆盖）"
    )

    @field_validator("mid")
    @classmethod
    def _valid_mid(cls, value: str | None) -> str | None:
        return _validate_mid_str(value)


class FeedbackAdminGetRequest(SQLModel):
    """管理端查看单条反馈详情"""

    report_id: str = Field(..., description="报告唯一标识")


class FeedbackAdminDeleteRequest(SQLModel):
    """管理端删除反馈（支持单条 / 批量）"""

    report_ids: list[str] = Field(
        ..., min_length=1, description="要删除的报告唯一标识列表"
    )


__all__ = [
    "FeedbackCategory",
    "FeedbackStatus",
    "FeedbackContext",
    "FeedbackSubmitRequest",
    "FeedbackListRequest",
    "FeedbackAdminListRequest",
    "FeedbackAdminCreateRequest",
    "FeedbackAdminUpdateRequest",
    "FeedbackAdminGetRequest",
    "FeedbackAdminDeleteRequest",
]
