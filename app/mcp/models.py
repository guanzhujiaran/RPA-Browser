"""MCP 调试服务的数据模型。

只描述「MCP 调试」自己的产出结构；执行结果等业务结构复用既有模型
（如 ``ActionResultResponse``），不在此重复定义。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.models.workflow.action_execution_models import ActionResultResponse


class StandardEnvelope(BaseModel):
    """RPA 服务统一响应壳（bili_common.models.response.StandardResponse）。"""

    code: int = 0
    msg: str = "success"
    data: JsonValue = None

    model_config = ConfigDict(extra="ignore")


class StepIssue(BaseModel):
    """单条静态校验问题（带可在 JSON 里定位的路径）。"""

    path: str = Field(description="步骤在 steps 中的位置，如 steps[2].children[0]")
    action_id: str = Field(default="", description="该步骤的 action_id")
    message: str = Field(description="校验失败原因")
    hint: str = Field(default="", description="修复建议")


class ValidationReport(BaseModel):
    """步骤静态校验结果。"""

    ok: bool = Field(description="是否通过（解析 + 编译都成功）")
    step_count: int = Field(default=0, description="顶层步骤数")
    total_steps: int = Field(default=0, description="含 children 的步骤总数")
    action_ids: list[str] = Field(
        default_factory=list, description="顶层步骤的 action_id 序列（便于核对）"
    )
    issues: list[StepIssue] = Field(default_factory=list, description="问题清单")
    compiled_nodes: int = Field(default=0, description="Pipeline 编译出的节点数")
    notes: list[str] = Field(default_factory=list, description="补充说明（如降级提示）")


class RunRecovery(BaseModel):
    """从日志中推断的「改哪一步」提示。"""

    action_name: str = Field(default="", description="失败动作名")
    action_id: str = Field(default="", description="失败动作 ID")
    order: int = Field(default=0, description="该动作在执行链路中的出现序号（0 基）")
    depth: int = Field(default=0, description="调用深度")
    candidate_path: str = Field(
        default="", description="候选 steps 路径，如 steps[3]（可能是循环体内的相对路径）"
    )
    ambiguous: bool = Field(
        default=False, description="是否无法唯一定位（同名重复 / 处于循环体内）"
    )
    error_message: str = Field(default="", description="失败信息（截断）")


class StatusCount(BaseModel):
    """按执行状态聚合的条数（避免对外暴露裸 dict）。"""

    status: str = Field(description="执行状态：success / failed / timeout")
    count: int = Field(description="该状态的日志条数")


class RunLogReport(BaseModel):
    """一次运行 / 一次执行的步骤日志汇总。"""

    execution_id: str = Field(default="", description="执行批次 ID")
    total: int = Field(default=0, description="该批次日志总条数")
    fetched: int = Field(default=0, description="实际拉取的条数")
    truncated: bool = Field(default=False, description="是否因上限被截断")
    status_counts: list[StatusCount] = Field(
        default_factory=list, description="按状态聚合的条数"
    )
    tree: str = Field(default="", description="按 depth 缩进的调用链文本")
    failed: list[JsonValue] = Field(
        default_factory=list, description="失败项明细（含 params / logs 摘要）"
    )
    recovery_hints: list[RunRecovery] = Field(
        default_factory=list, description="「改哪一步」的候选提示"
    )


class DebugActionItem(BaseModel):
    """动作列表项（MCP 工具输出用，字段精简）。"""

    action_id: str = Field(description="操作的雪花 action_id")
    name: str = Field(description="操作名称")
    steps_count: int = Field(default=0, description="顶层步骤数")
    is_enabled: bool = Field(default=True, description="是否启用")
    is_public: bool = Field(default=False, description="是否公开")
    updated_at: datetime | None = Field(default=None, description="最后更新时间")


class DebugActionDetail(BaseModel):
    """动作全量定义（DB 当前真值，steps 可直接改后再落库）。"""

    action_id: str = Field(description="操作的雪花 action_id")
    name: str = Field(description="操作名称")
    description: str = Field(default="", description="操作描述")
    step_count: int = Field(default=0, description="顶层步骤数")
    steps: list[JsonValue] = Field(default_factory=list, description="步骤列表")
    input_vars: list[JsonValue] = Field(default_factory=list, description="输入变量定义")
    output_vars: list[str] = Field(default_factory=list, description="输出变量名")
    parameters_schema: list[JsonValue] = Field(default_factory=list, description="参数定义")
    is_public: bool = Field(default=False, description="是否公开")
    is_enabled: bool = Field(default=True, description="是否启用")
    updated_at: datetime | None = Field(default=None, description="最后更新时间")


class DebugActionListData(BaseModel):
    """动作列表输出。"""

    total: int = Field(default=0, description="命中总数")
    items: list[DebugActionItem] = Field(default_factory=list, description="动作列表")


class DebugExecuteData(BaseModel):
    """执行输出（单个动作或内联步骤统一结构）。"""

    success: bool = Field(description="整体是否成功")
    execution_id: str = Field(default="", description="本次执行批次 ID，可下钻步骤日志")
    results: list[ActionResultResponse] = Field(
        default_factory=list, description="逐步骤结果"
    )
    validation: ValidationReport | None = Field(
        default=None, description="执行前的静态校验报告（内置动作不校验时为 null）"
    )
    blocked_by_validation: bool = Field(
        default=False, description="是否因静态校验不通过而被拦截（未执行）"
    )


class DebugSaveData(BaseModel):
    """落库输出（含回读一致性校验）。"""

    mode: str = Field(description="update（更新已有动作）/ import（新建）")
    action_id: str | None = Field(default=None, description="落库后的 action_id")
    name: str = Field(default="", description="动作名称")
    step_count: int = Field(default=0, description="写入的顶层步骤数")
    action_ids: list[str] = Field(default_factory=list, description="写入的步骤 action_id 序列")
    dry_run: bool = Field(default=False, description="是否仅预演未写库")
    verified: bool | None = Field(
        default=None, description="回读后 JSON 与 DB 是否一致（dry_run 时为 null）"
    )
    db_action_ids: list[str] = Field(
        default_factory=list, description="回读 DB 得到的步骤 action_id 序列"
    )
    validation: ValidationReport | None = Field(default=None, description="写入前静态校验")


class DebugRunData(BaseModel):
    """运行工作流（等待终态）输出。"""

    run_id: str = Field(description="运行 ID")
    workflow_id: str = Field(description="工作流雪花 ID")
    status: str = Field(default="running", description="终态：success/failed/interrupted/cancelled")
    finished: bool = Field(default=False, description="是否已到终态（false=等待超时）")
    execution_id: str = Field(default="", description="执行批次 ID，可喂给 rpa_debug_run_logs")
    total: int = Field(default=0, description="总步骤数")
    success_count: int = Field(default=0, description="成功步骤数")
    failed_count: int = Field(default=0, description="失败步骤数")
    error_message: str | None = Field(default=None, description="运行级错误信息")
    duration_ms: float = Field(default=0.0, description="运行耗时（毫秒）")
    waited_seconds: float = Field(default=0.0, description="本次等待耗时（秒）")
