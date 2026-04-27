"""
Workflow 模块 - 工作流请求/响应模型

定义工作流相关的 API 请求/响应模型（非数据库表模型）。
"""

from typing import Any, Dict, List
from datetime import datetime

from sqlmodel import SQLModel, Field

from app.models.core.workflow.models import (
    ActionTypeEnum,
    ErrorHandlingEnum,
    PluginHookEnum,
)


# ============ 工作流请求/响应 ============

class WorkflowStepRequest(SQLModel):
    """
    工作流步骤请求
    定义工作流中单个步骤的配置。
    """
    action_id: str = Field(description="操作ID，如 click, input, llm, my_custom_action")
    params: Dict[str, Any] = Field(default_factory=dict, description="操作参数，支持模板")
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


class WorkflowListRequest(SQLModel):
    """获取工作流列表请求"""
    skip: int = Field(default=0, description="跳过记录数")
    limit: int = Field(default=100, description="返回记录数")


class WorkflowExecuteRequest(SQLModel):
    """执行工作流请求"""
    name: str | None = Field(default=None, description="工作流名称")
    steps: List[WorkflowStepRequest] = Field(description="步骤列表")
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")
    on_error: str = Field(default="stop", description="错误处理")


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
    user_data: Dict[str, Any]
    is_enabled: bool
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
    created_at: datetime | None = None


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


class WorkflowExecuteResponse(SQLModel):
    """执行工作流响应"""
    execution_id: str
    results: List[Dict[str, Any]]
    summary: Dict[str, int]


# ============ 自定义操作请求/响应 ============

class CustomActionCreateRequest(SQLModel):
    """创建自定义操作请求"""
    name: str = Field(description="操作显示名称（必填）")
    action_type: str = Field(default="composite", description="操作类型: composite, code")
    description: str = Field(default="", description="操作描述")
    parameters_schema: List[Dict[str, Any]] = Field(default_factory=list)
    steps: List[Dict[str, Any]] = Field(default_factory=list, description="步骤列表JSON")
    tags: List[str] = Field(default_factory=list)
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")
    code: str | None = Field(default=None, description="自定义代码")


class CustomActionUpdateRequest(SQLModel):
    """更新自定义操作请求"""
    id: int = Field(description="操作数据库ID")
    name: str | None = Field(default=None, description="新名称")
    description: str | None = Field(default=None, description="新描述")
    parameters_schema: List[Dict[str, Any]] | None = Field(default=None)
    steps: List[Dict[str, Any]] | None = Field(default=None)
    tags: List[str] | None = Field(default=None)
    user_data: Dict[str, Any] | None = Field(default=None)
    code: str | None = Field(default=None)
    is_enabled: bool | None = Field(default=None)
    timeout: int | None = Field(default=None)


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
    user_data: Dict[str, Any]
    is_enabled: bool
    timeout: int
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
    created_at: datetime | None = None


class CustomActionCreateResponse(SQLModel):
    """创建自定义操作响应"""
    id: int
    action_id: str
    name: str


# ============ 操作执行请求/响应 ============

class ActionExecuteRequest(SQLModel):
    """执行操作请求"""
    action_id: str = Field(description="操作ID")
    params: Dict[str, Any] = Field(default_factory=dict, description="操作参数")
    user_data: Dict[str, Any] | None = Field(default=None, description="自定义数据")


class BatchActionRequest(SQLModel):
    """批量执行操作请求"""
    actions: List[ActionExecuteRequest] = Field(description="操作列表")
    parallel: bool = Field(default=False, description="是否并行执行")
    user_data: Dict[str, Any] | None = Field(default=None, description="共享自定义数据")


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


class ActionMetadataResponse(SQLModel):
    """操作元数据响应"""
    id: str
    name: str
    type: str
    description: str
    parameters: List[ActionParameterResponse]
    timeout: int = 30000
    requires_browser: bool = True


class ReloadActionsResponse(SQLModel):
    """重新加载响应"""
    loaded: int


__all__ = [
    # 枚举
    "ActionTypeEnum",
    "ErrorHandlingEnum",
    "PluginHookEnum",
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
    "WorkflowExecuteResponse",
    "CustomActionCreateRequest",
    "CustomActionUpdateRequest",
    "CustomActionDetailResponse",
    "CustomActionListItemResponse",
    "CustomActionCreateResponse",
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
    "ActionMetadataResponse",
    "ReloadActionsResponse",
]
