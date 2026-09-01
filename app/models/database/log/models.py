"""
Database 模块 - 浏览器操作日志模型

只有一张表：
    ActionLogRecord — 采集到的浏览器操作执行日志（可在数据库中查询）

日志采集「是否开启 / 采集哪些字段」由操作自身的基础配置决定：
    - 自定义操作（CompositeActionModel）带 log_enabled / log_record_* 等字段
    - 内置操作无独立配置，回落到服务端 settings.action_log_default_enabled
"""
from bili_common.models import StrEnumAutoDoc
from datetime import datetime
from typing import List

from sqlalchemy import Column, Index, JSON
from sqlmodel import Field, SQLModel


class ActionLogStatusEnum(StrEnumAutoDoc):
    """操作日志状态"""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ActionLogSourceEnum(StrEnumAutoDoc):
    """操作日志来源"""
    ACTION = "action"      # 单个操作执行
    WORKFLOW = "workflow"  # 工作流步骤执行
    PLUGIN = "plugin"      # 插件钩子执行


class ActionLogRecord(SQLModel, table=True):
    """浏览器操作执行日志"""

    __table_args__ = (
        Index("idx_action_log_mid_started", "mid", "started_at"),
        Index("idx_action_log_execution_order", "execution_id", "id"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    log_id: str = Field(max_length=64, unique=True, index=True, description="日志唯一标识")
    mid: str = Field(max_length=255, index=True, description="所属用户ID")

    execution_id: str = Field(default="", max_length=64, index=True, description="一次执行批次ID")
    parent_execution_id: str | None = Field(
        default=None, max_length=64, description="父执行批次ID（插件/嵌套调用）")
    depth: int = Field(default=0, description="嵌套深度")

    action_id: str = Field(max_length=100, index=True, description="操作ID")
    action_name: str = Field(default="", max_length=200, description="操作名称")
    action_type: str = Field(default="", max_length=64, description="操作类型")
    source: ActionLogSourceEnum = Field(
        default=ActionLogSourceEnum.ACTION, description="触发来源")

    workflow_id: str | None = Field(
        default=None, max_length=100, index=True, description="关联的工作流ID")
    browser_id: str = Field(default="", max_length=100, index=True, description="浏览器ID")
    session_id: str = Field(default="", max_length=100, description="会话ID")
    page_url: str = Field(default="", max_length=1000, description="执行时的页面URL")

    status: ActionLogStatusEnum = Field(
        default=ActionLogStatusEnum.SUCCESS, index=True, description="执行状态")
    success: bool = Field(default=False, index=True, description="是否成功")

    params: dict | None = Field(
        default=None, sa_column=Column(JSON), description="变量替换后的入参")
    result_data: dict | None = Field(
        default=None, sa_column=Column(JSON), description="执行返回结果")
    variables: dict | None = Field(
        default=None, sa_column=Column(JSON), description="变量池快照")
    logs: List[str] = Field(
        default_factory=list, sa_column=Column(JSON), description="执行过程日志")

    error_message: str | None = Field(default=None, max_length=2000, description="错误信息")
    execution_time: float = Field(default=0.0, description="执行耗时(秒)")

    started_at: datetime = Field(default_factory=datetime.now, index=True)
    finished_at: datetime | None = Field(default=None)


__all__ = [
    "ActionLogStatusEnum",
    "ActionLogSourceEnum",
    "ActionLogRecord",
]
