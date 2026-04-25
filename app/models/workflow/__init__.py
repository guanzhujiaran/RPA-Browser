"""
Workflow 模块 - 工作流相关模型

包含工作流、自定义操作、插件钩子等模型。
"""

from app.models.workflow.models import (
    # 枚举
    ActionTypeEnum,
    ErrorHandlingEnum,
    PluginHookEnum,
    # 数据库模型
    WorkflowStepModel,
    CustomActionModel,
    UserPluginModel,
    UserWorkflowModel,
    WorkflowExecutionLogModel,
    # 工作流请求/响应
    WorkflowStepRequest,
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowListRequest,
    WorkflowExecuteRequest,
    WorkflowDetailResponse,
    WorkflowListItemResponse,
    WorkflowCreateResponse,
    WorkflowDuplicateResponse,
    WorkflowExecuteResponse,
    # 自定义操作请求/响应
    CustomActionCreateRequest,
    CustomActionUpdateRequest,
    CustomActionDetailResponse,
    CustomActionListItemResponse,
    CustomActionCreateResponse,
    # 操作执行请求/响应
    ActionExecuteRequest,
    BatchActionRequest,
    ActionPreviewRequest,
    ActionValidateRequest,
    ExecuteStepRequest,
    ActionResultResponse,
    StepPreviewItem,
    ActionPreviewResponse,
    ActionValidateResponse,
    ExecuteStepResponse,
    # 插件请求/响应
    PluginCreateRequest as WorkflowPluginCreateRequest,
    PluginUpdateRequest as WorkflowPluginUpdateRequest,
    PluginDetailResponse as WorkflowPluginDetailResponse,
    PluginListItemResponse as WorkflowPluginListItemResponse,
    PluginMetadataResponse as WorkflowPluginMetadataResponse,
    PluginCreateResponse as WorkflowPluginCreateResponse,
    # 系统级模型
    ActionParameterResponse,
    ActionMetadataResponse,
    ReloadActionsResponse,
)

__all__ = [
    # 枚举
    "ActionTypeEnum",
    "ErrorHandlingEnum",
    "PluginHookEnum",
    # 数据库模型
    "WorkflowStepModel",
    "CustomActionModel",
    "UserPluginModel",
    "UserWorkflowModel",
    "WorkflowExecutionLogModel",
    # 工作流请求/响应
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
    # 自定义操作请求/响应
    "CustomActionCreateRequest",
    "CustomActionUpdateRequest",
    "CustomActionDetailResponse",
    "CustomActionListItemResponse",
    "CustomActionCreateResponse",
    # 操作执行请求/响应
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
    # 插件请求/响应
    "WorkflowPluginCreateRequest",
    "WorkflowPluginUpdateRequest",
    "WorkflowPluginDetailResponse",
    "WorkflowPluginListItemResponse",
    "WorkflowPluginMetadataResponse",
    "WorkflowPluginCreateResponse",
    # 系统级模型
    "ActionParameterResponse",
    "ActionMetadataResponse",
    "ReloadActionsResponse",
]
