"""
Workflow 模块 - 工作流请求/响应模型

定义工作流相关的 API 请求/响应模型（非数据库表模型）。
"""

from typing import Any, Dict, List
from datetime import datetime
from enum import Enum
from sqlmodel import SQLModel, Field
from app.models.base.base_sqlmodel import BasePaginationReq
from app.models.database.workflow.models import ActionType
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
    工作流步骤请求
    定义工作流中单个步骤的配置。
    """
    action_id: str = Field(description="操作ID，如 click, input, llm, my_custom_action")
    params: Dict[str, Any] = Field(default_factory=dict, description="操作参数，支持模板")
    children: List['WorkflowStepRequest'] | None = Field(default=None, description="子步骤列表（用于循环体或分支）")
    loop_count: int | None = Field(default=None, description="固定循环次数")
    loop_while: str | None = Field(default=None, description="条件循环，表达式为true时继续")
    loop_until: str | None = Field(default=None, description="条件退出，表达式为true时退出")
    retry: int = Field(default=0, description="失败重试次数")
    condition: str | None = Field(default=None, description="执行条件表达式")
    user_data: Dict[str, Any] | None = Field(default=None, description="步骤级自定义数据")


class WorkflowCreateRequest(SQLModel):
    """创建工作流请求"""
    name: str = Field(description="工作流显示名称（必填）")
    steps: List[WorkflowStepRequest] = Field(description="步骤列表")
    on_error: str = Field(default="stop", description="错误处理: stop=停止, continue=继续")
    description: str = Field(default="", description="工作流描述")
    tags: List[str] = Field(default_factory=list, description="标签列表")
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")
    enabled_plugins: List[str] = Field(default_factory=list, description="启用的插件ID列表")


class WorkflowUpdateRequest(SQLModel):
    """更新工作流请求"""
    id: int = Field(description="工作流数据库ID")
    name: str | None = Field(default=None, description="新名称")
    description: str | None = Field(default=None, description="新描述")
    steps: List[WorkflowStepRequest] | None = Field(default=None, description="新步骤列表")
    on_error: str | None = Field(default=None, description="错误处理策略")
    tags: List[str] | None = Field(default=None, description="标签列表")
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")
    is_enabled: bool | None = Field(default=None, description="是否启用")
    enabled_plugins: List[str] | None = Field(default=None, description="启用的插件ID列表")


class WorkflowListRequest(BasePaginationReq):
    """获取工作流列表请求"""
    filter_type: FilterType = Field(default=FilterType.ALL, description="筛选类型")
    sort_by: SortBy = Field(default=SortBy.UPDATED_AT, description="排序字段")
    sort_order: SortOrder = Field(default=SortOrder.DESC, description="排序方向")


class WorkflowExecuteRequest(SQLModel):
    """执行工作流请求"""
    name: str | None = Field(default=None, description="工作流名称")
    steps: List[WorkflowStepRequest] = Field(description="步骤列表")
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")
    on_error: str = Field(default="stop", description="错误处理")
    page_index: int | None = Field(default=None, description="页面索引，指定在哪个 tab 页执行操作")


class WorkflowDetailResponse(SQLModel):
    """工作流详情响应"""
    id: int
    workflow_id: str
    name: str
    version: str = "1.0.0"
    steps: List[Dict[str, Any]]
    on_error: str
    description: str
    tags: List[str]
    user_data: Dict[str, Any] | None = None
    enabled_plugins: List[str] = []
    is_enabled: bool
    is_public: bool = False
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowListItemResponse(SQLModel):
    """工作流列表项响应"""
    id: int
    workflow_id: str
    name: str
    description: str
    tags: List[str]
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


class WorkflowDuplicateResponse(SQLModel):
    """复制工作流响应"""
    id: int
    workflow_id: str
    name: str


class WorkflowForkRequest(SQLModel):
    """Fork 工作流请求"""
    id: int = Field(description="原工作流ID")
    new_name: str | None = Field(default=None, description="新名称，如果不提供则使用原名称 + ' (Fork)'")


class WorkflowForkResponse(SQLModel):
    """Fork 工作流响应"""
    id: int
    workflow_id: str
    name: str
    forked_from: str = Field(description="Fork 自哪个工作流")


class WorkflowExecuteResponse(SQLModel):
    """执行工作流响应"""
    execution_id: str
    results: List[Dict[str, Any]]
    summary: Dict[str, int]


class WorkflowStepExecuteRequest(SQLModel):
    """单步执行工作流请求"""
    browser_id: str = Field(description="浏览器会话 ID")
    step_index: int = Field(description="步骤索引（从 0 开始）")
    steps: List[WorkflowStepRequest] = Field(description="完整步骤列表")
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")
    page_index: int | None = Field(default=None, description="页面索引")


class WorkflowStepExecuteResponse(SQLModel):
    """单步执行工作流响应"""
    success: bool = Field(description="是否成功")
    step_index: int = Field(description="执行的步骤索引")
    action_id: str = Field(description="执行的动作 ID")
    result: Any = Field(default=None, description="执行结果")
    error: str | None = Field(default=None, description="错误信息")
    duration: float = Field(description="执行耗时（毫秒）")
    current_step: int = Field(description="当前执行到的步骤索引")
    total_steps: int = Field(description="总步骤数")


# ============ 自定义操作请求/响应 ============

class CustomActionCreateRequest(SQLModel):
    """创建自定义操作请求"""
    name: str = Field(description="操作显示名称（必填）")
    action_type: ActionType = Field(default=ActionType.COMPOSITE, description="操作类型")
    description: str = Field(default="", description="操作描述")
    parameters_schema: List[Dict[str, Any]] = Field(default_factory=list)
    steps: List[Dict[str, Any]] = Field(default_factory=list, description="步骤列表JSON")
    tags: List[str] = Field(default_factory=list)
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")
    is_public: bool = Field(default=False, description="是否公开给所有用户")
    enabled_plugins: List[str] = Field(default_factory=list, description="该动作内部引用的插件ID列表")


class CustomActionUpdateRequest(SQLModel):
    """更新自定义操作请求"""
    id: int = Field(description="操作数据库ID")
    name: str | None = Field(default=None, description="新名称")
    description: str | None = Field(default=None, description="新描述")
    parameters_schema: List[Dict[str, Any]] | None = Field(default=None)
    steps: List[Dict[str, Any]] | None = Field(default=None)
    tags: List[str] | None = Field(default=None)
    user_data: Dict[str, Any] | None = Field(default=None)
    is_enabled: bool | None = Field(default=None)
    is_public: bool | None = Field(default=None)
    timeout: int | None = Field(default=None)
    enabled_plugins: List[str] | None = Field(default=None)


class CustomActionListRequest(BasePaginationReq):
    """获取自定义操作列表请求"""
    filter_type: FilterType = Field(default=FilterType.ALL, description="筛选类型")
    sort_by: SortBy = Field(default=SortBy.UPDATED_AT, description="排序字段")
    sort_order: SortOrder = Field(default=SortOrder.DESC, description="排序方向")


class CustomActionDetailResponse(SQLModel):
    """自定义操作详情响应"""
    id: int
    action_id: str
    name: str
    version: str
    action_type: str
    description: str
    parameters_schema: List[Dict[str, Any]]
    steps: List[Dict[str, Any]]
    tags: List[str]
    user_data: Dict[str, Any] | None = None
    enabled_plugins: List[str] = []
    is_enabled: bool
    is_public: bool = False
    timeout: int
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CustomActionListItemResponse(SQLModel):
    """自定义操作列表项响应"""
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


class CustomActionCreateResponse(SQLModel):
    """创建自定义操作响应"""
    id: int
    action_id: str
    name: str


class ActionForkRequest(SQLModel):
    """Fork 自定义操作请求"""
    id: int = Field(description="原操作ID")
    new_name: str | None = Field(default=None, description="新名称，如果不提供则使用原名称 + ' (Fork)'")


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
    params: Dict[str, Any] = Field(default_factory=dict, description="操作参数")
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")
    page_index: int | None = Field(default=None, description="页面索引，指定在哪个 tab 页执行操作")
    

class BatchActionRequest(SQLModel):
    """批量执行操作请求"""
    actions: List[ActionExecuteRequest] = Field(description="操作列表")
    parallel: bool = Field(default=False, description="是否并行执行")
    user_data: Dict[str, Any] | None = Field(default=None, description="共享自定义数据")
    page_index: int | None = Field(default=None, description="页面索引，指定在哪个 tab 页执行操作")


class ActionPreviewRequest(SQLModel):
    """预览参数替换请求"""
    action_id: str = Field(description="操作ID")
    params: Dict[str, Any] = Field(default_factory=dict, description="参数")
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")


class ActionValidateRequest(SQLModel):
    """验证参数请求"""
    action_id: str = Field(description="操作ID")
    params: Dict[str, Any] = Field(default_factory=dict, description="待验证参数")
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")


class ExecuteStepRequest(SQLModel):
    """单步执行请求"""
    action_id: str = Field(description="操作ID")
    params: Dict[str, Any] = Field(default_factory=dict, description="操作参数")
    step_index: int = Field(default=0, description="步骤索引")
    page_index: int | None = Field(default=None, description="页面索引，指定在哪个 tab 页执行操作")


class ActionResultResponse(SQLModel):
    """操作执行结果"""
    success: bool
    data: Any = None
    error: str | None = None
    execution_time: float = 0.0
    action_id: str = ""


class StepPreviewItem(SQLModel):
    """步骤预览项"""
    step_index: int
    action_id: str
    original_params: Dict[str, Any]
    replaced_params: Dict[str, Any]


class ActionPreviewResponse(SQLModel):
    """预览响应"""
    action_id: str
    action_name: str
    is_composite: bool
    steps_preview: List[StepPreviewItem]
    replaced_params: Dict[str, Any]
    found_params: List[str]


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


# ============ 系统级模型 ============

class ActionParameterResponse(SQLModel):
    """操作参数响应"""
    name: str
    type: str
    required: bool
    default: Any | None = None
    description: str = ""
    # SQLModel 验证规则（根据类型设置，数值类型用 min/max，字符串类型用 min_length/max_length）
    min: float | None = Field(default=None, description="最小值（仅数值类型：int/float），字符串类型为 None")
    max: float | None = Field(default=None, description="最大值（仅数值类型：int/float），字符串类型为 None")
    min_length: int | None = Field(default=None, description="最小长度（仅字符串类型：str），数值类型为 None")
    max_length: int | None = Field(default=None, description="最大长度（仅字符串类型：str），数值类型为 None")
    enum: List[Any] | None = Field(default=None, description="枚举值列表，无枚举时为 None")
    format: str | None = Field(default=None, description="格式要求（如 email, uri 等），无格式要求时为 None")

class ReloadActionsResponse(SQLModel):
    """重新加载响应"""
    loaded: int


# ============ 插件挂载相关模型 ============

class PluginCreateRequest(SQLModel):
    """创建插件挂载请求"""
    name: str = Field(description="插件名称")
    hook_type: str = Field(description="钩子类型: before_action, after_action, on_error")
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
    new_name: str | None = Field(default=None, description="新名称，如果不提供则使用原名称 + ' (Fork)'")


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
    # 请求/响应
    "WorkflowStepRequest",
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
    "WorkflowStepExecuteRequest",
    "WorkflowStepExecuteResponse",
    "CustomActionCreateRequest",
    "CustomActionUpdateRequest",
    "CustomActionDetailResponse",
    "CustomActionListItemResponse",
    "CustomActionCreateResponse",
    "ActionForkRequest",
    "ActionForkResponse",
    "ActionExecuteRequest",
    "BatchActionRequest",
    "ActionPreviewRequest",
    "ActionValidateRequest",
    "ExecuteStepRequest",
    "ActionResultResponse",
    "StepPreviewItem",
    "ActionPreviewResponse",
    "ActionValidateResponse",
    "ExecuteStepResponse",
    "ActionParameterResponse",
    "ReloadActionsResponse",
    "PluginCreateRequest",
    "PluginUpdateRequest",
    "PluginDetailResponse",
    "PluginListItemResponse",
    "PluginListRequest",
    "PluginForkRequest",
    "PluginForkResponse",
]
