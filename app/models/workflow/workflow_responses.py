"""Workflow 模块 - 工作流响应模型（详情/列表/运行记录/Fork/资源记录）

从 ``app.models/workflow/models.py`` 拆出（原 1141 行单文件）。
历史入口 ``models.py`` 保留为 re-export 门面，既有 import 路径不变。
"""

from __future__ import annotations

from bili_common.core.time_utils import OptionalUnixTime
from bili_common.models import StrEnumAutoDoc
from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.action_params import VariableSpec
from app.models.execution.condition_models import ConditionRule
from app.models.base.base_sqlmodel import BasePaginationReq
from dataclasses import dataclass
from datetime import datetime
from pydantic import Field
from typing import Any, Dict
from sqlmodel import SQLModel


from app.models.workflow.enums import FilterType, SortBy, SortOrder
from app.models.workflow.mixins import UpstreamSyncFields
from app.models.workflow.step_models import WorkflowStepRequest


class WorkflowDetailResponse(UpstreamSyncFields):
    """工作流详情响应"""

    id: int
    workflow_id: int
    name: str
    custom_action_id: int | None = None
    description: str
    browser_id: int | None = None
    trigger_type: str
    trigger_config: Dict
    is_enabled: bool
    is_public: bool = False
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    next_run_at: datetime | None = None
    notify_inbox_on_start: bool = Field(
        default=False, description="站内通知：开始执行时是否发送"
    )
    notify_inbox_on_end: bool = Field(
        default=True, description="站内通知：结束时（成功/失败/停止）是否发送"
    )
    notify_push_on_start: bool = Field(
        default=False, description="站外推送：开始执行时是否发送"
    )
    notify_push_on_end: bool = Field(
        default=True, description="站外推送：结束时（成功/失败/停止）是否发送"
    )
    notify_on_start: bool = Field(
        default=False, description="【已废弃】开始通知（通道混合，= 两通道取或）"
    )
    notify_on_end: bool = Field(
        default=True, description="【已废弃】结束通知（通道混合，= 两通道取或）"
    )
    log_enabled: bool | None = None
    log_debug: bool | None = None
    resume_on_restart: bool = False
    browser_ids: list[int] | None = Field(
        default=None, description="执行目标浏览器ID列表；为空=单浏览器"
    )
    concurrent_exec: bool = Field(default=False, description="多浏览器时是否并发执行")
    variables: Dict = Field(
        default_factory=dict, description="输入变量值 {name: value}"
    )
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowListItemResponse(UpstreamSyncFields):
    """工作流列表项响应"""

    id: int
    workflow_id: int
    name: str
    custom_action_id: int | None = None
    description: str
    browser_id: int | None = None
    trigger_type: str = "manual"
    trigger_config: dict = Field(default_factory=dict)
    is_enabled: bool
    is_public: bool = False
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    running_run_id: str | None = Field(
        default=None, description="当前进行中的运行ID（RUNNING），无进行中的运行则为空"
    )
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    next_run_at: datetime | None = None
    notify_inbox_on_start: bool = Field(
        default=False, description="站内通知：开始执行时是否发送"
    )
    notify_inbox_on_end: bool = Field(
        default=True, description="站内通知：结束时（成功/失败/停止）是否发送"
    )
    notify_push_on_start: bool = Field(
        default=False, description="站外推送：开始执行时是否发送"
    )
    notify_push_on_end: bool = Field(
        default=True, description="站外推送：结束时（成功/失败/停止）是否发送"
    )
    notify_on_start: bool = Field(
        default=False, description="【已废弃】开始通知（通道混合，= 两通道取或）"
    )
    notify_on_end: bool = Field(
        default=True, description="【已废弃】结束通知（通道混合，= 两通道取或）"
    )
    log_enabled: bool | None = None
    log_debug: bool | None = None
    resume_on_restart: bool = False
    browser_ids: list[int] | None = Field(
        default=None, description="执行目标浏览器ID列表；为空=单浏览器"
    )
    concurrent_exec: bool = Field(default=False, description="多浏览器时是否并发执行")
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowAdminListItemResponse(WorkflowListItemResponse):
    """管理端工作流列表项响应（附带作者 mid）"""

    mid: str = Field(default="", description="工作流作者 mid")


class WorkflowRunLogItemResponse(SQLModel):
    """工作流运行记录列表项"""

    id: int
    run_id: str
    workflow_id: int
    browser_id: int = 0
    trigger_source: str
    status: str
    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    #: 执行批次ID（工作流运行场景恒等于 run_id，供按值下钻 ActionLogRecord 步骤日志）
    execution_id: str = ""
    error_message: str | None = None
    duration_ms: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # 运行期进度（与 SSE 快照同源；running 行用于渲染进度条）
    completed_steps: int = 0
    current_step_index: int = 0
    current_step_name: str = ""
    loop_action_name: str = ""
    loop_index: int = 0
    loop_total: int = 0
    loop_body_completed: int = 0
    loop_body_total: int = 0
    loop_body_step_name: str = ""
    progress_updated_at: OptionalUnixTime = None
    #: 排队位置（1 基）；0 = 未排队（执行中或已结束）。见 docs/同浏览器工作流排队计划书.md
    queue_position: int = 0
    # 断点恢复
    resumable: bool = False
    resume_of: str | None = None
    resume_count: int = 0
    checkpoint_step: int = 0


class WorkflowRunLogDetailResponse(WorkflowRunLogItemResponse):
    """工作流运行记录详情（携带 execution_id = run_id，供下钻步骤日志）"""


class WorkflowRunStopResponse(SQLModel):
    """停止工作流运行响应（立刻停止 + 同步停止定期执行）。

    见 ``docs/工作流停止计划书.md``：停止不再等步骤边界 / 宽限期，而是直接
    ``task.cancel()`` 中断；同时关闭该工作流的定期执行（cron 任务被移除）。
    """

    run_id: str = Field(description="运行ID")
    status: str = Field(description="该运行当前 / 最终状态")
    stopping: bool = Field(default=False, description="是否已下发停止")
    forced: bool = Field(default=False, description="是否直接强制中断执行 task")
    already_finished: bool = Field(default=False, description="该运行此前已结束")
    queue_cancelled: bool = Field(default=False, description="该运行当时在排队，已被取消")
    reaped: bool = Field(default=False, description="僵尸运行记录已被清理")
    schedule_stopped: bool = Field(
        default=False, description="本次是否关闭了该工作流的定期执行"
    )
    cancelled_runs: list[str] = Field(
        default_factory=list, description="一并停止的同工作流其它运行ID"
    )


class WorkflowCreateResponse(SQLModel):
    """创建工作流响应"""

    id: int
    workflow_id: int
    name: str
    message: str = Field(default="创建成功", description="提示信息")


class WorkflowDuplicateResponse(SQLModel):
    """复制工作流响应"""

    id: int
    workflow_id: int
    name: str
    message: str = Field(default="复制成功", description="提示信息")


class WorkflowForkResponse(SQLModel):
    """Fork 工作流响应"""

    id: int
    workflow_id: int
    name: str
    forked_from: int = Field(description="Fork 自哪个工作流（雪花 int）")


class WorkflowSyncResponse(SQLModel):
    """同步工作流响应"""

    workflow_id: int
    updated: list[str] = Field(
        default_factory=list, description="内容已更新的动作名称列表"
    )
    created: list[str] = Field(
        default_factory=list, description="新 Fork 的动作名称列表"
    )
    unavailable: list[str] = Field(
        default_factory=list, description="上游已不可用的动作 ID 列表"
    )
    workflow_config_updated: bool = Field(
        default=False, description="工作流触发配置是否有更新"
    )
    local_overwritten: bool = Field(
        default=False, description="本次同步是否覆盖了本地修改（触发配置或动作内容）"
    )


class WorkflowExecuteResponse(SQLModel):
    """执行工作流响应"""

    execution_id: str
    status: str = Field(default="started", description="执行状态")
    message: str = Field(default="开始执行", description="提示信息")
    results: list[Dict] = Field(default_factory=list, description="执行结果")
    summary: dict[str, int] = Field(default_factory=dict, description="执行摘要")


class WorkflowResourceRecordItemResponse(SQLModel):
    """工作流已处理资源记录列表项

    `id` 为记录主键（自增），`resource_id` 为业务主键（动态ID/预约ID等）。
    """

    id: int
    workflow_id: int
    browser_id: int = 0
    resource_type: str
    resource_id: str
    status: str
    process_count: int = 1
    fail_reason: str | None = None
    execution_id: str | None = None
    extra: dict[str, Any] | None = None
    first_processed_at: datetime | None = None
    last_processed_at: datetime | None = None


class WorkflowResourceRecordDeleteResponse(SQLModel):
    """删除已处理资源记录的结果

    `deleted` 在 `dry_run=true` 时表示「将会删除」的条数。
    """

    deleted: int = 0
    dry_run: bool = False

