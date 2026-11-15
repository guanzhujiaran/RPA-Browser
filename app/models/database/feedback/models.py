"""
用户报错 / 反馈数据库模型

定义「用户提交报错 / 反馈」的数据库表模型。落库后由服务层弱依赖推送服主
（见 docs/用户提交报错与实时推送服主计划书.md）。

反馈**允许未登录提交**：``mid`` 可空，登录态额外快照提交者昵称 ``uname``，
未登录用户建议填写 ``contact``（邮箱 / QQ 等）便于服主回访。
"""

from pydantic import NaiveDatetime
from sqlmodel import Field, Column
from sqlalchemy import Text as SQLText, BIGINT

from app.models.base.base_sqlmodel import BaseSQLModel


class RpaErrorReportBase(BaseSQLModel):
    """用户报错 / 反馈基础模型"""

    # 报告唯一标识（雪花/uuid），供通知幂等与回查
    report_id: str = Field(index=True, unique=True, max_length=64)

    # 提交者 mid（未登录提交为 None）
    mid: int | None = Field(default=None, index=True, sa_type=BIGINT)

    # 提交者昵称快照（登录态由网关注入的 x-bili-uname，未登录为 None）
    uname: str | None = Field(default=None, max_length=128)

    # 联系方式（未登录用户填写邮箱 / QQ 等，便于服主回访）
    contact: str | None = Field(default=None, max_length=256)

    # 分类：bug / suggestion / other
    category: str = Field(default="bug", max_length=32)

    # 简短标题
    title: str = Field(max_length=256)

    # 详细描述（错误现象 / 期望）
    description: str = Field(sa_column=Column(SQLText))

    # 错误上下文 JSON（source/action_id/workflow_id/run_id/browser_id/error_message/error_detail/page_url/extra）
    context: str = Field(default="", sa_column=Column(SQLText))

    # 处理状态：pending / resolved / ignored
    status: str = Field(default="pending", max_length=32, index=True)

    # 管理端处理备注（处理结论 / 沟通记录）
    admin_remark: str = Field(default="", sa_column=Column(SQLText))

    # 处理人 mid（管理端最近一次处理反馈的管理员）
    handled_by: int | None = Field(default=None, sa_type=BIGINT)

    # 处理时间（首次置为非 pending 的时间；naive datetime，与 BaseTimestamp 口径一致）
    handled_at: NaiveDatetime | None = Field(default=None)


class RpaErrorReport(RpaErrorReportBase, table=True):
    __table_args__ = {
        "sqlite_autoincrement": True,
        "extend_existing": True,
    }
    id: int | None = Field(
        default=None, primary_key=True, sa_column_kwargs={"autoincrement": True}
    )


__all__ = [
    "RpaErrorReportBase",
    "RpaErrorReport",
]
