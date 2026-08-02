"""
浏览器操作日志 - API 请求/响应模型

仅包含日志记录的查询/删除/统计。
（采集「是否启用 / 采集哪些字段」已直接落到 action 的基础配置上，无需单独的配置模型。）
"""
from typing import Any, Dict, List, Optional

from datetime import datetime

from sqlmodel import SQLModel
from pydantic import Field

from app.models.database.log.models import (
    ActionLogSourceEnum,
    ActionLogStatusEnum,
)
from app.models.base.base_sqlmodel import BasePaginationReq


# ============ 日志记录查询 ============


class ActionLogListRequest(BasePaginationReq):
    """日志查询请求（支持筛选 + 分页）"""
    action_id: str | None = Field(default=None, description="按操作ID筛选")
    execution_id: str | None = Field(default=None, description="按执行批次ID筛选")
    workflow_id: str | None = Field(default=None, description="按工作流ID筛选")
    browser_id: str | None = Field(default=None, description="按浏览器ID筛选")
    source: ActionLogSourceEnum | None = Field(default=None, description="按触发来源筛选")
    status: ActionLogStatusEnum | None = Field(default=None, description="按执行状态筛选")
    success: bool | None = Field(default=None, description="按是否成功筛选")
    keyword: str | None = Field(default=None, description="关键字（匹配操作名/操作ID/错误信息）")
    started_after: datetime | None = Field(default=None, description="起始时间（含）")
    started_before: datetime | None = Field(default=None, description="结束时间（含）")
    order_desc: bool = Field(default=True, description="是否按时间倒序")


class ActionLogItemResponse(SQLModel):
    """单条日志记录响应"""
    id: int | None = None
    log_id: str
    mid: str
    execution_id: str
    parent_execution_id: str | None = None
    depth: int
    action_id: str
    action_name: str
    action_type: str
    source: ActionLogSourceEnum
    workflow_id: str | None = None
    browser_id: str
    session_id: str
    page_url: str
    status: ActionLogStatusEnum
    success: bool
    params: Dict | None = None
    result_data: Dict | None = None
    variables: Dict | None = None
    logs: List[str] = Field(default_factory=list)
    error_message: str | None = None
    execution_time: float
    started_at: datetime
    finished_at: datetime | None = None


class ActionLogDetailResponse(ActionLogItemResponse):
    """日志详情响应（当前与列表项一致，便于后续扩展）"""
    pass


class ActionLogByExecutionRequest(SQLModel):
    """按执行批次查询完整链路"""
    execution_id: str = Field(description="执行批次ID")


class ActionLogDeleteRequest(SQLModel):
    """按 log_id 批量删除"""
    log_ids: List[str] = Field(default_factory=list, description="要删除的日志唯一ID列表")


class ActionLogClearRequest(SQLModel):
    """按条件批量清理日志"""
    action_id: str | None = None
    execution_id: str | None = None
    workflow_id: str | None = None
    browser_id: str | None = None
    source: ActionLogSourceEnum | None = None
    status: ActionLogStatusEnum | None = None
    success: bool | None = None
    keyword: str | None = None
    started_after: datetime | None = None
    started_before: datetime | None = None


class ActionLogStatsResponse(SQLModel):
    """日志统计响应"""
    days: int
    total: int
    success: int
    failed: int
    items: List[Dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "ActionLogListRequest",
    "ActionLogItemResponse",
    "ActionLogDetailResponse",
    "ActionLogByExecutionRequest",
    "ActionLogDeleteRequest",
    "ActionLogClearRequest",
    "ActionLogStatsResponse",
]
