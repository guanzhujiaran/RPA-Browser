"""Workflow 模块 - 工作流请求模型（CRUD/调度/Fork/资源记录）

从 ``app.models/workflow/models.py`` 拆出（原 1141 行单文件）。
历史入口 ``models.py`` 保留为 re-export 门面，既有 import 路径不变。
"""

from __future__ import annotations

from bili_common.models import StrEnumAutoDoc
from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.action_params import VariableSpec
from app.models.execution.condition_models import ConditionRule
from app.models.base.base_sqlmodel import BasePaginationReq
from app.models.workflow.enums import FilterType, SortBy, SortOrder
from dataclasses import dataclass
from datetime import datetime
from pydantic import Field
from sqlmodel import SQLModel
from app.models.workflow.step_models import WorkflowStepRequest


class WorkflowCreateRequest(SQLModel):
    """创建工作流请求 - 工作流是调度外壳，只引用已有动作"""

    name: str = Field(description="工作流显示名称（必填）")
    custom_action_id: int | None = Field(
        default=None, description="要引用的自定义动作ID（雪花 int，多对一共享）"
    )
    description: str = Field(default="", description="工作流描述")
    browser_id: int | None = Field(
        default=None, description="执行目标浏览器ID（定时触发必填）"
    )
    trigger_type: str = Field(
        default="manual", max_length=50, description="触发类型: manual/cron"
    )
    trigger_config: dict = Field(
        default_factory=dict, description="触发配置，cron 触发时为 {cron: 表达式}"
    )
    is_public: bool = Field(default=False, description="是否公开给所有用户")
    notify_on_start: bool = Field(
        default=False, description="开始执行时是否发送通知"
    )
    notify_on_end: bool = Field(
        default=True, description="结束时是否发送通知（成功/失败）"
    )
    log_enabled: bool | None = Field(
        default=None,
        description="执行时是否记录步骤级操作日志（不传=未设置，听从动作/项目开关；可在运行记录中下钻，仅保留最近7天）",
    )
    resume_on_restart: bool = Field(
        default=False,
        description="服务重启/异常退出后是否自动恢复本次被中断的运行（至少一次语义）",
    )
    browser_ids: list[int] | None = Field(
        default=None,
        description="执行目标浏览器ID列表；为空=仅用 browser_id（单浏览器）",
    )
    concurrent_exec: bool = Field(
        default=False,
        description="多浏览器时是否并发执行（False=按列表顺序串行）",
    )
    variables: dict = Field(
        default_factory=dict,
        description=(
            "输入变量值 {name: value}：运行期覆盖被引用动作声明的变量默认值；"
            "变量定义以动作契约/声明为准，工作流只存值"
        ),
    )


class WorkflowUpdateRequest(SQLModel):
    """更新工作流请求"""

    id: int = Field(description="工作流数据库ID")
    name: str | None = Field(default=None, description="新名称")
    custom_action_id: int | None = Field(
        default=None, description="要引用的自定义动作ID（雪花 int）"
    )
    description: str | None = Field(default=None, description="新描述")
    browser_id: int | None = Field(
        default=None, description="执行目标浏览器ID（不传表示不修改）"
    )
    trigger_type: str | None = Field(default=None, description="触发类型")
    trigger_config: dict | None = Field(default=None, description="触发配置")
    is_public: bool | None = Field(default=None, description="是否公开")
    is_enabled: bool | None = Field(default=None, description="是否启用")
    notify_on_start: bool | None = Field(
        default=None, description="开始执行时是否发送通知（不传表示不修改）"
    )
    notify_on_end: bool | None = Field(
        default=None, description="结束时是否发送通知（不传表示不修改）"
    )
    log_enabled: bool | None = Field(
        default=None, description="执行时是否记录步骤级操作日志（不传表示不修改）"
    )
    resume_on_restart: bool | None = Field(
        default=None,
        description="服务重启后是否自动恢复本次被中断的运行（不传表示不修改）",
    )
    auto_sync_upstream: bool | None = Field(
        default=None,
        description="是否开启上游自动同步（不传表示不修改；仅 Fork 资源有意义）",
    )
    browser_ids: list[int] | None = Field(
        default=None,
        description="执行目标浏览器ID列表（不传表示不修改；传空列表=回到单浏览器模式）",
    )
    concurrent_exec: bool | None = Field(
        default=None,
        description="多浏览器时是否并发执行（不传表示不修改）",
    )
    variables: dict | None = Field(
        default=None, description="输入变量值 {name: value}（不传表示不修改）"
    )


class WorkflowListRequest(BasePaginationReq):
    """获取工作流列表请求"""

    filter_type: FilterType = Field(default=FilterType.ALL, description="筛选类型")
    sort_by: SortBy = Field(default=SortBy.UPDATED_AT, description="排序字段")
    sort_order: SortOrder = Field(default=SortOrder.DESC, description="排序方向")
    browser_id: int | None = Field(
        default=None,
        description="按执行目标浏览器过滤（不传不过滤）；与 filter_type 条件叠加",
    )


class WorkflowExecuteRequest(SQLModel):
    """执行工作流请求 - 支持内联步骤或引用已保存操作"""

    browser_id: int = Field(default=1, description="浏览器ID")
    action_id: int | None = Field(
        default=None, description="要执行的自定义操作ID（雪花 int，可选）"
    )
    workflow_id: int | None = Field(
        default=None, description="工作流ID（雪花 int）"
    )
    steps: list[WorkflowStepRequest] | None = Field(
        default=None, description="内联步骤列表（不提供 action_id 时使用）"
    )
    name: str | None = Field(default=None, description="工作流名称（用于内联步骤）")
    variables: dict = Field(default_factory=dict, description="变量池")
    input_data: dict = Field(default_factory=dict, description="输入数据")
    output_vars: list[str] = Field(default_factory=list, description="输出变量名称列表")
    on_error: str = Field(default="stop", description="错误处理")
    page_index: int | None = Field(
        default=None, description="页面索引，指定在哪个 tab 页执行操作"
    )


class WorkflowAdminListRequest(BasePaginationReq):
    """管理端工作流列表请求（root 全量视图）"""

    name: str | None = Field(default=None, description="按名称模糊搜索")
    mid: int | None = Field(default=None, description="按作者 mid 过滤")
    is_public: bool | None = Field(default=None, description="按公开状态过滤")
    is_enabled: bool | None = Field(default=None, description="按启用状态过滤")
    sort_by: SortBy = Field(default=SortBy.UPDATED_AT, description="排序字段")
    sort_order: SortOrder = Field(default=SortOrder.DESC, description="排序方向")


class WorkflowAdminSetEnabledRequest(SQLModel):
    """管理端启停工作流请求"""

    id: int = Field(description="工作流数据库ID")
    is_enabled: bool = Field(description="目标启用状态")


class WorkflowRunNowRequest(SQLModel):
    """立即运行已保存的工作流请求"""

    id: int = Field(description="工作流数据库ID")
    browser_id: int | None = Field(
        default=None,
        description="运行目标浏览器ID，不传则使用工作流上配置的 browser_id",
    )


class WorkflowRunStopRequest(SQLModel):
    """停止正在运行的工作流请求"""

    run_id: str = Field(description="运行ID（run_id）")


class WorkflowRunResumeRequest(SQLModel):
    """继续运行（断点恢复）请求"""

    run_id: str = Field(description="被中断的运行ID")


class WorkflowRunLogListRequest(SQLModel):
    """工作流运行记录列表请求"""

    workflow_id: int = Field(description="工作流ID（雪花 int）")
    page: int = Field(default=1, description="页码")
    per_page: int = Field(default=10, description="每页数量")


class WorkflowForkRequest(SQLModel):
    """Fork 工作流请求"""

    id: int = Field(description="原工作流ID")
    new_name: str | None = Field(
        default=None, description="新名称，如果不提供则使用原名称 + ' (Fork)'"
    )
    auto_sync_upstream: bool = Field(
        default=False, description="是否开启上游自动同步（默认关闭）"
    )


class WorkflowSyncRequest(SQLModel):
    """同步工作流到上游最新版本请求"""

    id: int = Field(description="当前 Fork 副本的工作流 ID")


class WorkflowResourceRecordListRequest(BasePaginationReq):
    """工作流已处理资源记录查询请求"""

    workflow_id: int = Field(description="工作流ID")
    browser_id: int | None = Field(
        default=None,
        description="按浏览器过滤；不传=该工作流全部浏览器",
    )
    resource_type: str | None = Field(default=None, description="资源类型命名空间")
    status: str | None = Field(
        default=None, description="处理状态: success/skipped/failed；不传=全部"
    )
    # 业务主键（模糊）/ 所属运行（精确）
    resource_id: str | None = Field(
        default=None, description="业务主键，支持模糊匹配（LIKE %keyword%）"
    )
    run_id: str | None = Field(default=None, description="所属运行ID（精确）")
    # 处理次数区间
    process_count_min: int | None = Field(default=None, description="处理次数下限（含）")
    process_count_max: int | None = Field(default=None, description="处理次数上限（含）")
    # 最近处理时间区间（ISO 字符串）
    processed_from: datetime | None = Field(
        default=None, description="最近处理时间起始（含）"
    )
    processed_to: datetime | None = Field(
        default=None, description="最近处理时间截止（含）"
    )

