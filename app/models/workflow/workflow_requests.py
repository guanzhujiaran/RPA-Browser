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
from pydantic import Field, model_validator
from sqlmodel import SQLModel
from typing import Any, Self
from app.models.workflow.step_models import WorkflowStepRequest


class WorkflowNotifyMatrixRequest(SQLModel):
    """运行通知「通道 × 时机」矩阵入参（见 docs/通知动作与运行告警计划书.md §E）

    新字段 ``notify_{inbox,push}_on_{start,end}`` 是唯一语义来源；旧字段
    ``notify_on_start`` / ``notify_on_end`` 已废弃，仅在对应新字段**未显式传入**时
    按「双通道」展开——用 ``model_fields_set`` 判定，不把默认值误当用户意图。
    """

    notify_on_start: bool | None = Field(
        default=None,
        description=(
            "【已废弃】通道混合的开始通知开关；请改用 notify_inbox_on_start / "
            "notify_push_on_start（仅在对应新字段未传时按双通道展开）"
        ),
    )
    notify_on_end: bool | None = Field(
        default=None,
        description=(
            "【已废弃】通道混合的结束通知开关；请改用 notify_inbox_on_end / "
            "notify_push_on_end（仅在对应新字段未传时按双通道展开）"
        ),
    )
    notify_inbox_on_start: bool | None = Field(
        default=None, description="站内通知：开始执行时是否发送"
    )
    notify_inbox_on_end: bool | None = Field(
        default=None, description="站内通知：结束时（成功/失败/停止）是否发送"
    )
    notify_push_on_start: bool | None = Field(
        default=None, description="站外推送：开始执行时是否发送"
    )
    notify_push_on_end: bool | None = Field(
        default=None, description="站外推送：结束时（成功/失败/停止）是否发送"
    )

    @model_validator(mode="after")
    def _expand_deprecated_notify_flags(self) -> Self:
        """旧字段兼容：按双通道展开到对应时机，新字段显式传入时以新字段为准"""
        provided = self.model_fields_set
        for legacy, inbox, push in (
            ("notify_on_start", "notify_inbox_on_start", "notify_push_on_start"),
            ("notify_on_end", "notify_inbox_on_end", "notify_push_on_end"),
        ):
            if legacy not in provided:
                continue
            value = getattr(self, legacy)
            if value is None:
                continue
            if inbox not in provided:
                setattr(self, inbox, value)
            if push not in provided:
                setattr(self, push, value)
        return self


class WorkflowCreateRequest(WorkflowNotifyMatrixRequest):
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
    log_enabled: bool | None = Field(
        default=None,
        description="执行时是否记录步骤级操作日志（不传=未设置，听从动作/项目开关；可在运行记录中下钻，仅保留最近7天）",
    )
    log_debug: bool | None = Field(
        default=None,
        description=(
            "调试模式（不传=未设置）：开启后本次运行强制全量采集——入参/结果/"
            "变量全记、成功也记、payload 不截断；与 log_enabled 独立，debug 隐含开启"
        ),
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


class WorkflowUpdateRequest(WorkflowNotifyMatrixRequest):
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
    notify_inbox_on_start: bool | None = Field(
        default=None, description="站内通知：开始执行时是否发送（不传表示不修改）"
    )
    notify_inbox_on_end: bool | None = Field(
        default=None, description="站内通知：结束时是否发送（不传表示不修改）"
    )
    notify_push_on_start: bool | None = Field(
        default=None, description="站外推送：开始执行时是否发送（不传表示不修改）"
    )
    notify_push_on_end: bool | None = Field(
        default=None, description="站外推送：结束时是否发送（不传表示不修改）"
    )
    log_enabled: bool | None = Field(
        default=None, description="执行时是否记录步骤级操作日志（不传表示不修改）"
    )
    log_debug: bool | None = Field(
        default=None,
        description="调试模式：强制全量采集（不传表示不修改）",
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
        default=None,
        description="输入变量值 {name: value}（整体替换；不传表示不修改；与 variables_patch 互斥）",
    )
    variables_patch: dict | None = Field(
        default=None,
        description=(
            "输入变量增量更新 {name: value}：只覆盖给出的键，值为 null 表示删除该键；"
            "与 variables 互斥（见 docs/工作流变量增量更新计划书.md）"
        ),
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

    browser_id: int = Field(
        default=1,
        description=(
            "仅兼容旧调用保留，不参与决策：目标浏览器取 query 的 browser_id"
            "（verify_browser_ownership 校验归属）"
        ),
    )
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
        description=(
            "仅兼容旧调用保留，不参与决策：运行目标浏览器一律取 query 的 browser_id"
            "（verify_browser_ownership 校验归属，缺失直接 422）"
        ),
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


class WorkflowIdRequest(SQLModel):
    """按工作流数据库 ID 定位（详情 / 导出 / 删除共用）

    单独建模型而不用裸 ``dict``：裸 dict 在 OpenAPI 里只能生成无 ``properties``
    的 object，MCP 工具因此拿不到参数（调用只能发空 body）。
    """

    id: int = Field(description="工作流数据库 ID（雪花 int，字符串形式也接受）")


class WorkflowDuplicateRequest(SQLModel):
    """复制工作流请求"""

    id: int = Field(description="被复制的工作流数据库 ID（雪花 int）")
    new_name: str = Field(description="副本名称（必填，避免与原工作流同名混淆）")


class WorkflowRunDetailRequest(SQLModel):
    """按 run_id 获取运行记录详情请求"""

    run_id: str = Field(description="运行 ID（run_id，运行记录与步骤日志的主键）")


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
    execution_id: str | None = Field(
        default=None,
        description="执行批次ID（精确；= 工作流运行 run_id，与操作日志 execution_id 同名同值）",
    )
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


class WorkflowResourceRecordCreateRequest(SQLModel):
    """手工新增/更新一条「已处理资源」记录请求

    用途：修正去重数据（例如某条动态实际没参与却被误记，先删掉；或确认已线下参与，
    需要提前把它加入去重集合）。写入走 upsert（唯一键 = 工作流 + 浏览器 + 类型 + 业务主键），
    已存在时更新状态并累加 `process_count`。
    """

    workflow_id: int = Field(description="工作流ID")
    resource_type: str = Field(
        max_length=64, description="资源类型命名空间（需与取数去重的 resource_type 一致）"
    )
    resource_id: str = Field(
        max_length=128, description="资源业务主键（如动态 dynId、预约 sid）"
    )
    browser_id: int | None = Field(
        default=None,
        description="执行浏览器ID；不传=使用工作流配置的浏览器（去重按浏览器隔离）",
    )
    status: str | None = Field(
        default=None,
        description="处理状态: success/skipped/failed；不传=success（success/skipped 参与去重）",
    )
    fail_reason: str | None = Field(
        default=None, max_length=500, description="备注/跳过原因（success 时不保存）"
    )
    extra: dict[str, Any] | None = Field(
        default=None, description="附加快照信息（可选，存入记录的 extra 字段）"
    )


class WorkflowResourceRecordDeleteRequest(SQLModel):
    """删除「已处理资源」记录请求（支持单条 / 勾选批量 / 按运行清理）

    安全约束：除 `workflow_id` 外必须至少给一个条件，服务端会拒绝无条件全量删除；
    `dry_run=true` 只返回将删除的条数，供前端二次确认。
    """

    workflow_id: int = Field(description="工作流ID")
    ids: list[int] | None = Field(
        default=None, description="记录主键列表（列表页勾选后删除）"
    )
    browser_id: int | None = Field(default=None, description="按浏览器过滤")
    resource_type: str | None = Field(default=None, description="按资源类型过滤")
    status: str | None = Field(
        default=None, description="按状态过滤: success/skipped/failed"
    )
    resource_id: str | None = Field(
        default=None, description="按业务主键精确匹配（非模糊）"
    )
    execution_id: str | None = Field(
        default=None, description="按执行批次ID过滤（清理某一轮运行写入的记录）"
    )
    dry_run: bool = Field(
        default=False, description="true=只统计将删除的条数，不实际删除"
    )

