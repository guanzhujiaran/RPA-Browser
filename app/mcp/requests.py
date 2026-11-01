"""MCP 调试路由的请求模型。

约定：执行 / 校验类接口一律接收**未规范化**的 actions steps（原始 JSON），
由服务端用 ``workflow_step_adapter`` 做校验，避免 MCP 客户端猜 DSL 结构。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoosePayload(BaseModel):
    """任意 JSON 对象（字段由动作 DSL 决定，不做约束）。

    为什么不直接用 ``JsonValue``：它的 schema 是自引用的 ``$ref``，
    MCP 客户端/转换器会把请求参数退化成单一类型（string / array），
    导致 ``steps``、``content`` 这类对象入参在参数校验阶段就被拒。
    这里显式声明为 object（``additionalProperties: true``）。
    """

    model_config = ConfigDict(extra="allow")

    def to_json(self) -> object:
        """转回普通 JSON 结构（供服务层处理）。"""
        return self.model_dump(mode="python")


class ListActionsRequest(BaseModel):
    """按名称搜索动作。"""

    keyword: str | None = Field(default=None, description="名称关键字（模糊匹配）")
    page: int = Field(default=1, ge=1, description="页码")
    per_page: int = Field(default=20, ge=1, le=100, description="每页数量")


class GetActionRequest(BaseModel):
    """导出动作定义。"""

    action_id: str = Field(description="操作的雪花 action_id（字符串，避免精度丢失）")


class ValidateStepsRequest(BaseModel):
    """静态校验：内联 steps 或已保存动作，二选一。"""

    steps: list[LoosePayload] | None = Field(
        default=None, description="待校验的步骤数组（与动作 JSON 的 steps 同结构）"
    )
    action_id: str | None = Field(
        default=None, description="校验已保存的动作（雪花 action_id 字符串）"
    )


class ExecuteDebugRequest(BaseModel):
    """执行动作或内联步骤（默认先做静态校验）。

    ``browser_id`` 由 query 参数提供（服务端据此校验浏览器归属）。
    """

    action_id: str | None = Field(
        default=None, description="已保存动作 ID：内置动作为名字符串，复合动作为雪花 id 字符串"
    )
    steps: list[LoosePayload] | None = Field(
        default=None, description="内联步骤（不落库直接试跑）；与 action_id 二选一"
    )
    params: LoosePayload | None = Field(default=None, description="操作参数（action_id 模式）")
    variables: LoosePayload | None = Field(default=None, description="变量池")
    output_vars: list[str] = Field(default_factory=list, description="输出变量名称列表")
    page_index: int | None = Field(default=None, description="目标标签页索引")
    force: bool = Field(default=False, description="跳过静态校验强制执行")


class SaveActionRequest(BaseModel):
    """落库动作定义（同名 upsert），落库后自动回读比对。"""

    content: LoosePayload = Field(
        description="完整动作定义（动作 JSON 内容：name / steps / input_vars / ...）"
    )
    action_id: str | None = Field(
        default=None, description="指定要更新的动作（雪花 id 字符串）；不传则按 name 匹配"
    )
    dry_run: bool = Field(default=False, description="仅预演，不写库")


class RunWorkflowRequest(BaseModel):
    """运行工作流并等待终态。"""

    workflow_id: str = Field(description="工作流雪花 ID（字符串，避免精度丢失）")
    timeout_seconds: int = Field(default=300, ge=1, le=3600, description="最长等待秒数")


class RunLogsRequest(BaseModel):
    """按运行 / 执行批次查步骤日志。"""

    run_id: str | None = Field(default=None, description="运行 ID（与 execution_id 同值）")
    execution_id: str | None = Field(default=None, description="执行批次 ID（工作流运行里 = run_id）")
    max_items: int = Field(default=200, ge=1, le=2000, description="最多拉取的日志条数")
    only_failed: bool = Field(default=False, description="只返回失败步骤")
    detail_limit: int = Field(default=10, ge=0, le=50, description="失败项下钻详情的条数上限")


class RecentActionLogsRequest(BaseModel):
    """查某动作最近的执行日志。"""

    action_id: str = Field(description="操作 ID（内置动作名或雪花 id 字符串）")
    limit: int = Field(default=10, ge=1, le=100, description="返回条数")


class RecentActionLogItem(BaseModel):
    """动作日志摘要项。"""

    log_id: str = Field(default="", description="日志 ID（可查详情）")
    execution_id: str = Field(default="", description="执行批次 ID")
    action_name: str = Field(default="", description="动作名称")
    status: str = Field(default="", description="执行状态")
    success: bool = Field(default=False, description="是否成功")
    error_message: str | None = Field(default=None, description="错误信息")
    execution_time: float = Field(default=0.0, description="耗时（秒）")


class RecentActionLogsData(BaseModel):
    """动作日志摘要列表。"""

    total: int = Field(default=0, description="总条数")
    items: list[RecentActionLogItem] = Field(default_factory=list, description="日志列表")
