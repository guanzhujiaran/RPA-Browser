"""
用户报错 / 反馈数据库模型

定义「用户提交报错 / 反馈」的数据库表模型。落库后由服务层弱依赖推送服主
（见 docs/用户提交报错与实时推送服主计划书.md）。
"""

from sqlmodel import Field, Column
from sqlalchemy import Text as SQLText, BIGINT

from app.models.base.base_sqlmodel import BaseSQLModel


class RpaErrorReportBase(BaseSQLModel):
    """用户报错 / 反馈基础模型"""

    # 报告唯一标识（雪花/uuid），供通知幂等与回查
    report_id: str = Field(index=True, unique=True, max_length=64)

    # 提交者 mid
    mid: int = Field(index=True, sa_type=BIGINT)

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
