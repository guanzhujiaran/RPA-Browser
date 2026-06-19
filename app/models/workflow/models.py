"""
Workflow 模块 - 工作流请求/响应模型

定义工作流相关的 API 请求/响应模型（非数据库表模型）。
"""
from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.action_params import PluginConfig
from app.models.execution.condition_models import ConditionRule
from typing import Any, Dict, List
from datetime import datetime
from sqlmodel import SQLModel
from pydantic import Field
from app.models.base.base_sqlmodel import BasePaginationReq
from enum import StrEnum


class FilterType(StrEnum):
    ALL = "all"
    PRIVATE = "private"
    PUBLIC = "public"
    COMMUNITY = "community"
    VERIFIED = "verified"


class SortBy(StrEnum):
    UPDATED_AT = "updated_at"
    LIKES_COUNT = "likes_count"
    FORKS_COUNT = "forks_count"
    CREATED_AT = "created_at"
    NAME = "name"


class SortOrder(StrEnum):
    DESC = "desc"
    ASC = "asc"


class WorkflowStepRequest(SQLModel):
    """
    工作流步骤请求 - 与 WorkflowStep 模型对齐，与 BaseAction 创建参数对齐
    定义工作流中单个步骤的配置。
    """
    action_id: str = Field(
        description="操作ID，如 click, input, llm, my_composite_action")
    action_type: BuiltinActionType | str | None = Field(
        default=None, description="操作类型")
    mid: int | None = Field(default=None, description="用户ID")
    params: Dict = Field(
        default_factory=dict, description="操作参数，支持 {{变量名}} 模板替换")
    retry: int = Field(default=0, description="失败重试次数")
    condition: ConditionRule | None = Field(default=None, description="执行条件规则（结构化条件，不使用 eval）")
    output_var: str | None = Field(default=None, description="结果变量键名")
    input_vars: Dict = Field(
        default_factory=dict, description="输入变量")
    output_vars: List[str] = Field(
        default_factory=list, description="输出变量名称列表")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    children: List['WorkflowStepRequest'] | None = Field(
        default=None, description="子步骤列表（用于循环体或分支）")
    loop_count: int | None = Field(default=None, description="固定循环次数")
    loop_while: str | None = Field(
        default=None, description="条件循环，表达式为true时继续")
    loop_until: str | None = Field(
        default=None, description="条件退出，表达式为true时退出")


class WorkflowStepResponse(SQLModel):
    """工作流步骤响应 - 与 WorkflowStep 模型对齐，与 BaseAction 创建参数对齐"""
    action_id: str = Field(description="操作ID")
    action_type: BuiltinActionType | str | None = Field(
        default=None, description="操作类型")
    mid: int | None = Field(default=None, description="用户ID")
    params: Dict = Field(description="操作参数")
    retry: int = Field(default=0, description="失败重试次数")
    condition: ConditionRule | None = Field(default=None, description="执行条件规则")
    output_var: str | None = Field(default=None, description="结果变量键名")
    input_vars: Dict = Field(
        default_factory=dict, description="输入变量")
    output_vars: List[str] = Field(
        default_factory=list, description="输出变量名称列表")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    children: List['WorkflowStepResponse'] | None = Field(
        default=None, description="子步骤列表")
    loop_count: int | None = Field(default=None, description="固定循环次数")
    loop_while: str | None = Field(default=None, description="条件循环")
    loop_until: str | None = Field(default=None, description="条件退出")


class WorkflowCreateRequest(SQLModel):
    """创建工作流请求"""
    name: str = Field(description="工作流显示名称（必填）")
    custom_action_id: str | None = Field(
        default=None, description="要执行的自定义动作ID")
    description: str = Field(default="", description="工作流描述")
    trigger_type: str = Field(
        default="manual", max_length=50, description="触发类型: manual/cron")
    trigger_config: Dict = Field(
        default_factory=dict, description="触发配置")
    is_public: bool = Field(default=False, description="是否公开给所有用户")
    enabled_plugins: List[PluginConfig] | None = Field(
        default=None, description="关联的插件列表")


class WorkflowUpdateRequest(SQLModel):
    """更新工作流请求"""
    id: int = Field(description="工作流数据库ID")
    name: str | None = Field(default=None, description="新名称")
    custom_action_id: str | None = Field(default=None, description="要执行的自定义动作ID")
    description: str | None = Field(default=None, description="新描述")
    trigger_type: str | None = Field(default=None, description="触发类型")
    trigger_config: Dict | None = Field(
        default=None, description="触发配置")
    is_public: bool | None = Field(default=None, description="是否公开")
    is_enabled: bool | None = Field(default=None, description="是否启用")
    enabled_plugins: List[PluginConfig] | None = Field(
        default=None, description="关联的插件列表")


class WorkflowListRequest(BasePaginationReq):
    """获取工作流列表请求"""
    filter_type: FilterType = Field(default=FilterType.ALL, description="筛选类型")
    sort_by: SortBy = Field(default=SortBy.UPDATED_AT, description="排序字段")
    sort_order: SortOrder = Field(default=SortOrder.DESC, description="排序方向")


class WorkflowExecuteRequest(SQLModel):
    """执行工作流请求 - 支持内联步骤或引用已保存操作"""
    browser_id: int = Field(default=1, description="浏览器ID")
    action_id: str | None = Field(default=None, description="要执行的自定义操作ID（可选）")
    workflow_id: str | None = Field(default=None, description="工作流ID（用于关联插件）")
    steps: List[WorkflowStepRequest] | None = Field(
        default=None, description="内联步骤列表（不提供 action_id 时使用）")
    name: str | None = Field(default=None, description="工作流名称（用于内联步骤）")
    variables: Dict = Field(default_factory=dict, description="变量池")
    input_data: Dict = Field(
        default_factory=dict, description="输入数据")
    output_vars: List[str] = Field(default_factory=list, description="输出变量名称列表")
    on_error: str = Field(default="stop", description="错误处理")
    page_index: int | None = Field(
        default=None, description="页面索引，指定在哪个 tab 页执行操作")


class WorkflowDetailResponse(SQLModel):
    """工作流详情响应"""
    id: int
    workflow_id: str
    name: str
    custom_action_id: str | None = None
    description: str
    trigger_type: str
    trigger_config: Dict
    is_enabled: bool
    is_public: bool = False
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    enabled_plugins: List[PluginConfig] = Field(default_factory=list, description="关联的插件列表")
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowListItemResponse(SQLModel):
    """工作流列表项响应"""
    id: int
    workflow_id: str
    name: str
    custom_action_id: str | None = None
    description: str
    is_enabled: bool
    is_public: bool = False
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowCreateResponse(SQLModel):
    """创建工作流响应"""
    id: int
    workflow_id: str
    name: str
    message: str = Field(default="创建成功", description="提示信息")


class WorkflowDuplicateResponse(SQLModel):
    """复制工作流响应"""
    id: int
    workflow_id: str
    name: str
    message: str = Field(default="复制成功", description="提示信息")


class WorkflowForkRequest(SQLModel):
    """Fork 工作流请求"""
    id: int = Field(description="原工作流ID")
    new_name: str | None = Field(
        default=None, description="新名称，如果不提供则使用原名称 + ' (Fork)'")


class WorkflowForkResponse(SQLModel):
    """Fork 工作流响应"""
    id: int
    workflow_id: str
    name: str
    forked_from: str = Field(description="Fork 自哪个工作流")


class WorkflowExecuteResponse(SQLModel):
    """执行工作流响应"""
    execution_id: str
    status: str = Field(default="started", description="执行状态")
    message: str = Field(default="开始执行", description="提示信息")
    results: List[Dict] = Field(
        default_factory=list, description="执行结果")
    summary: Dict[str, int] = Field(default_factory=dict, description="执行摘要")


# ============ 自定义操作请求/响应 ============

class InputVarDefinition(SQLModel):
    """输入变量定义"""
    name: str = Field(description="变量名称")
    type: str = Field(default="string",
                      description="变量类型: string/number/boolean/array/object")
    default: Any | None = Field(default=None, description="默认值")
    required: bool = Field(default=False, description="是否必填")
    description: str = Field(default="", description="变量描述")


class CompositeActionCreateRequest(SQLModel):
    """创建复合操作请求 - 与 CompositeActionModel 数据库模型对齐"""
    name: str = Field(description="操作显示名称（必填）")
    action_type: BuiltinActionType = Field(
        default=BuiltinActionType.COMPOSITE, description="操作类型")
    description: str = Field(default="", description="操作描述")
    parameters_schema: List[Dict] = Field(
        default_factory=list, description="参数定义JSON")
    steps: List[Dict] = Field(
        default_factory=list, description="步骤列表JSON")
    tags: List[str] = Field(default_factory=list, description="标签列表")
    input_vars: List[InputVarDefinition] = Field(
        default_factory=list, description="输入变量定义")
    output_vars: List[str] = Field(
        default_factory=list, description="输出变量名称列表")
    is_public: bool = Field(default=False, description="是否公开给所有用户")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False, description="错误时重试")
    retry_times: int = Field(default=0, description="重试次数")
    retry_delay: float = Field(default=1.0, description="重试延迟(秒)")


class CompositeActionUpdateRequest(SQLModel):
    """更新复合操作请求"""
    id: int = Field(description="操作数据库ID")
    name: str | None = Field(default=None, description="新名称")
    description: str | None = Field(default=None, description="新描述")
    parameters_schema: List[Dict] | None = Field(
        default=None, description="参数定义JSON")
    steps: List[Dict] | None = Field(
        default=None, description="步骤列表JSON")
    tags: List[str] | None = Field(default=None, description="标签列表")
    input_vars: List[InputVarDefinition] | None = Field(
        default=None, description="输入变量定义")
    output_vars: List[str] | None = Field(default=None, description="输出变量名称列表")
    is_enabled: bool | None = Field(default=None, description="是否启用")
    is_public: bool | None = Field(default=None, description="是否公开")
    timeout: int | None = Field(default=None, description="超时时间(毫秒)")
    retry_on_error: bool | None = Field(default=None, description="错误时重试")
    retry_times: int | None = Field(default=None, description="重试次数")
    retry_delay: float | None = Field(default=None, description="重试延迟(秒)")


class CompositeActionListRequest(BasePaginationReq):
    """获取复合操作列表请求"""
    filter_type: FilterType = Field(default=FilterType.ALL, description="筛选类型")
    sort_by: SortBy = Field(default=SortBy.UPDATED_AT, description="排序字段")
    sort_order: SortOrder = Field(default=SortOrder.DESC, description="排序方向")


class CompositeActionDetailResponse(SQLModel):
    """复合操作详情响应"""
    id: int
    action_id: str
    name: str
    version: str
    action_type: str
    description: str
    mid: str = ""
    parameters_schema: List[Dict]
    steps: List[Dict]
    tags: List[str]
    input_vars: List[InputVarDefinition]
    output_vars: List[str]
    is_enabled: bool
    is_public: bool = False
    timeout: int
    retry_on_error: bool
    retry_times: int
    retry_delay: float
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CompositeActionListItemResponse(SQLModel):
    """复合操作列表项响应"""
    id: int
    action_id: str
    name: str
    action_type: str
    description: str
    steps_count: int
    is_enabled: bool
    is_public: bool = False
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CompositeActionCreateResponse(SQLModel):
    """创建复合操作响应"""
    id: int
    action_id: str
    name: str


class ActionForkRequest(SQLModel):
    """Fork 自定义操作请求"""
    id: int = Field(description="原操作ID")
    new_name: str | None = Field(
        default=None, description="新名称，如果不提供则使用原名称 + ' (Fork)'")


class ActionForkResponse(SQLModel):
    """Fork 自定义操作响应"""
    id: int
    action_id: str
    name: str
    forked_from: str = Field(description="Fork 自哪个操作")


# ============ 操作执行请求/响应 ============

class ActionExecuteRequest(SQLModel):
    """执行操作请求"""
    action_id: str = Field(description="操作ID")
    params: Dict = Field(default_factory=dict, description="操作参数")
    variables: Dict = Field(default_factory=dict, description="变量池")
    input_vars: Dict = Field(
        default_factory=dict, description="输入变量，会被合并到 variables 变量池中")
    output_vars: List[str] = Field(default_factory=list, description="输出变量名称列表")
    page_index: int | None = Field(
        default=None, description="页面索引，指定在哪个 tab 页执行操作")

    @property
    def action_type(self) -> BuiltinActionType:
        if self.action_id in BuiltinActionType:
            return BuiltinActionType(self.action_id)
        return BuiltinActionType.COMPOSITE  # 自定义操作默认为复合操作


class ActionPreviewRequest(SQLModel):
    """预览参数替换请求"""
    action_id: str = Field(description="操作ID")
    params: Dict = Field(default_factory=dict, description="参数")
    input_vars: Dict = Field(
        default_factory=dict, description="输入变量，会被合并到预览变量池中")


class ActionValidateRequest(SQLModel):
    """验证参数请求"""
    action_id: str = Field(description="操作ID")
    params: Dict = Field(default_factory=dict, description="待验证参数")
    input_vars: Dict = Field(
        default_factory=dict, description="输入变量，会被合并到变量池中")


class ExecuteStepRequest(SQLModel):
    """单步执行请求"""
    action_id: str = Field(description="操作ID")
    params: Dict = Field(default_factory=dict, description="操作参数")
    variables: Dict = Field(default_factory=dict, description="变量池")
    step_index: int = Field(default=0, description="步骤索引")
    page_index: int | None = Field(
        default=None, description="页面索引，指定在哪个 tab 页执行操作")


class ActionResultResponse(SQLModel):
    """操作执行结果"""
    success: bool
    data: Any = None
    error: str | None = None
    execution_time: float = 0.0
    action_id: str = ""
    action_name: str = ""
    variables: dict = Field(default_factory=dict, description="执行后的全局变量")
    replaced_params: dict = Field(default_factory=dict, description="变量替换后的实际调用参数")


class StepPreviewItem(SQLModel):
    """步骤预览项 — 支持递归展开控制流分支"""
    step_index: int
    action_id: str
    original_params: Dict
    replaced_params: Dict
    input_vars: Dict = Field(default_factory=dict, description="输入变量")
    output_vars: List[str] = Field(default_factory=list, description="输出变量名称列表")
    preview_variables: Dict = Field(default_factory=dict, description="该步骤模拟后的变量")
    branches: dict | None = Field(default=None, description="if-else 分支配对 {true: [...], false: [...]}")
    loop_preview: list | None = Field(default=None, description="循环体预览步骤列表")
    children: list[dict] | None = Field(default=None, description="复合动作子步骤预览列表")


class ActionPreviewResponse(SQLModel):
    """预览响应"""
    action_id: str
    action_name: str
    is_composite: bool
    steps_preview: List[StepPreviewItem]
    replaced_params: Dict
    found_params: List[str]
    preview_result: Dict = Field(default_factory=dict, description="模拟执行结果数据")
    preview_variables: Dict = Field(default_factory=dict, description="模拟执行后的变量池")


class ActionValidateResponse(SQLModel):
    """验证响应"""
    valid: bool
    action_id: str
    action_name: str
    missing_params: List[str]
    invalid_params: List[str]
    errors: List[str]


class ExecuteStepResponse(SQLModel):
    """单步执行响应"""
    step_index: int
    action_id: str
    action_name: str
    result: ActionResultResponse


class WorkflowStepExecuteRequest(SQLModel):
    """单步执行工作流请求"""
    browser_id: str = Field(description="浏览器ID")
    steps: List[WorkflowStepRequest] = Field(description="步骤列表")
    step_index: int = Field(description="要执行的步骤索引")
    user_data: Dict = Field(default_factory=dict, description="用户数据")
    page_index: int | None = Field(default=None, description="页面索引")


class WorkflowStepExecuteResponse(SQLModel):
    """单步执行工作流响应"""
    success: bool
    step_index: int
    action_id: str
    result: Any = None
    error: str | None = None
    duration: float = 0.0
    current_step: int
    total_steps: int


# ============ 系统级模型 ============

class ActionParameterResponse(SQLModel):
    """操作参数响应"""
    name: str
    type: str
    required: bool
    default: Any | None = None
    description: str = ""
    # SQLModel 验证规则（根据类型设置，数值类型用 min/max，字符串类型用 min_length/max_length）
    min: float | None = Field(
        default=None, description="最小值（仅数值类型：int/float），字符串类型为 None")
    max: float | None = Field(
        default=None, description="最大值（仅数值类型：int/float），字符串类型为 None")
    min_length: int | None = Field(
        default=None, description="最小长度（仅字符串类型：str），数值类型为 None")
    max_length: int | None = Field(
        default=None, description="最大长度（仅字符串类型：str），数值类型为 None")
    enum: List[Any] | None = Field(
        default=None, description="枚举值列表，无枚举时为 None")
    format: str | None = Field(
        default=None, description="格式要求（如 email, uri 等），无格式要求时为 None")


class ReloadActionsResponse(SQLModel):
    """重新加载响应"""
    loaded: int


# ============ 插件挂载相关模型 ============

class PluginCreateRequest(SQLModel):
    """创建插件挂载请求 - 与 UserPlugin 数据库模型对齐"""
    name: str = Field(description="插件名称")
    hook_type: str = Field(
        description="钩子类型: before_action, after_action, on_success, on_error, on_timeout")
    custom_action_id: str = Field(description="要执行的自定义动作ID")
    description: str = Field(default="", description="描述")
    priority: int = Field(default=100, description="优先级")
    is_public: bool = Field(default=False, description="是否公开")


class PluginUpdateRequest(SQLModel):
    """更新插件挂载请求"""
    id: int = Field(description="插件ID")
    name: str | None = Field(default=None)
    description: str | None = Field(default=None)
    hook_type: str | None = Field(default=None)
    custom_action_id: str | None = Field(default=None)
    priority: int | None = Field(default=None)
    is_enabled: bool | None = Field(default=None)
    is_public: bool | None = Field(default=None)


class PluginDetailResponse(SQLModel):
    """插件详情响应"""
    id: int
    plugin_id: str
    name: str
    hook_type: str
    custom_action_id: str
    description: str
    is_enabled: bool
    priority: int
    is_public: bool
    forks_count: int = 0
    forked_from_id: int | None = None


class PluginListItemResponse(SQLModel):
    """插件列表项响应"""
    id: int
    plugin_id: str
    name: str
    hook_type: str
    custom_action_id: str
    is_enabled: bool
    priority: int
    is_public: bool = False
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PluginListRequest(BasePaginationReq):
    """获取插件列表请求"""
    filter_type: FilterType = Field(default=FilterType.ALL, description="筛选类型")
    sort_by: SortBy = Field(default=SortBy.UPDATED_AT, description="排序字段")
    sort_order: SortOrder = Field(default=SortOrder.DESC, description="排序方向")


class PluginForkRequest(SQLModel):
    """Fork 插件请求"""
    id: int = Field(description="原插件ID")
    new_name: str | None = Field(
        default=None, description="新名称，如果不提供则使用原名称 + ' (Fork)'")


class PluginForkResponse(SQLModel):
    """Fork 插件响应"""
    id: int
    plugin_id: str
    name: str
    forked_from: str = Field(description="Fork 自哪个插件")


__all__ = [
    # 枚举
    "FilterType",
    "SortBy",
    "SortOrder",
    # 自定义操作辅助模型
    "InputVarDefinition",
    # 工作流
    "WorkflowCreateRequest",
    "WorkflowUpdateRequest",
    "WorkflowListRequest",
    "WorkflowExecuteRequest",
    "WorkflowDetailResponse",
    "WorkflowListItemResponse",
    "WorkflowCreateResponse",
    "WorkflowDuplicateResponse",
    "WorkflowForkRequest",
    "WorkflowForkResponse",
    "WorkflowExecuteResponse",
    # 复合操作
    "CompositeActionCreateRequest",
    "CompositeActionUpdateRequest",
    "CompositeActionListRequest",
    "CompositeActionDetailResponse",
    "CompositeActionListItemResponse",
    "CompositeActionCreateResponse",
    "ActionForkRequest",
    "ActionForkResponse",
    # 操作执行
    "ActionExecuteRequest",
    "ActionPreviewRequest",
    "ActionValidateRequest",
    "ExecuteStepRequest",
    "ActionResultResponse",
    "StepPreviewItem",
    "ActionPreviewResponse",
    "ActionValidateResponse",
    "ExecuteStepResponse",
    # 工作流步骤执行
    "WorkflowStepExecuteRequest",
    "WorkflowStepExecuteResponse",
    # 系统级
    "ActionParameterResponse",
    "ReloadActionsResponse",
    # 插件
    "PluginCreateRequest",
    "PluginUpdateRequest",
    "PluginDetailResponse",
    "PluginListItemResponse",
    "PluginListRequest",
    "PluginForkRequest",
    "PluginForkResponse",
]
