"""执行模块模型兼容入口（aggregator）

历史模块，1884 行的单文件已按职责拆分为以下模块：

===========================  ============================================
模块                         职责
===========================  ============================================
``builtin_action_types.py``  内置动作枚举 / 名称 / 描述 / 图标 / OnErrorEnum
``variable_specs.py``        变量契约（VariableSpec、模板扫描、契约推导）
``action_metadata.py``       操作元数据模型（ActionMetadata 等）
``action_param_models.py``   操作参数模型（21 个 *Params + MAP）
``action_result_models.py``  操作结果模型（21 个 *Result + MAP）
``workflow_step_models.py``  工作流步骤模型（判别联合 / 适配器 / 工厂）
===========================  ============================================

本文件仅做**全量 re-export**，保证
``from app.models.execution.action_params import X`` 既有引用零改动；
新代码请直接从上述子模块导入。
"""

from __future__ import annotations

from typing import TypeVar

from app.models.execution.action_metadata import (
    ActionMetadata,
    ActionMetadataResponse,
    ActionParameter,
    build_action_metadata,
)
from app.models.execution.action_param_models import (
    ActionLogOption,
    AllActionParams,
    BaseActionParams,
    BiliReserveParams,
    BUILTIN_ACTION_PARAMS_MAP,
    ClickParams,
    CompositeParams,
    FetchExternalDataParams,
    GetTextParams,
    GetWindowParams,
    HoverParams,
    IfElseParams,
    InputParams,
    LLMParams,
    LoopParams,
    MarkResourceResultParams,
    MouseActionParams,
    NavigateParams,
    NewPageParams,
    PickLineParams,
    Position,
    PrintParams,
    RandomPickParams,
    ScreenshotParams,
    ScrollParams,
    SelectorActionParams,
    ShuffleListParams,
    SortListParams,
    WaitParams,
)
from app.models.execution.action_result_models import (
    AllActionResult,
    BiliReserveResult,
    BUILTIN_ACTION_RESULT_MAP,
    ClickResult,
    CompositeResult,
    FetchExternalDataResult,
    GetTextResult,
    GetWindowResult,
    HoverResult,
    IfElseResult,
    InputResult,
    LLMResult,
    LoopResult,
    MarkResourceResultResult,
    NavigateResult,
    NewPageResult,
    PickLineResult,
    PrintResult,
    RandomPickResult,
    ScreenshotResult,
    ScrollResult,
    ShuffleListResult,
    SortListResult,
    WaitResult,
)
from app.models.execution.builtin_action_types import (
    BUILTIN_ACTION_ICON_SERIES,
    BuiltinActionDesc,
    BuiltinActionIconId,
    BuiltinActionName,
    BuiltinActionType,
    OnErrorEnum,
)
from app.models.execution.variable_specs import (
    VariableSpec,
    VariableType,
    compute_action_variable_contract,
    extract_template_variables,
)
from app.models.execution.workflow_step_models import (
    BaseWorkflowStep,
    BiliReserveWorkflowStep,
    ClickWorkflowStep,
    CompositeWorkflowStep,
    FetchExternalDataWorkflowStep,
    GetTextWorkflowStep,
    GetWindowWorkflowStep,
    HoverWorkflowStep,
    IfElseWorkflowStep,
    InputWorkflowStep,
    LLMWorkflowStep,
    LoopWorkflowStep,
    MarkResourceResultWorkflowStep,
    NavigateWorkflowStep,
    NewPageWorkflowStep,
    PickLineWorkflowStep,
    PrintWorkflowStep,
    RandomPickWorkflowStep,
    ScreenshotWorkflowStep,
    ScrollWorkflowStep,
    ShuffleListWorkflowStep,
    SortListWorkflowStep,
    WaitWorkflowStep,
    WorkflowStep,
    _ensure_action_type,
    _fill_action_type_impl,
    _WORKFLOW_STEP_CLASS_MAP,
    create_workflow_step,
    is_composite_action_id,
    normalize_composite_action_id,
    workflow_step_adapter,
)

__all__ = [
    # builtin_action_types
    "BUILTIN_ACTION_ICON_SERIES",
    "BuiltinActionDesc",
    "BuiltinActionIconId",
    "BuiltinActionName",
    "BuiltinActionType",
    "OnErrorEnum",
    # variable_specs
    "VariableSpec",
    "VariableType",
    "compute_action_variable_contract",
    "extract_template_variables",
    # action_metadata
    "ActionMetadata",
    "ActionMetadataResponse",
    "ActionParameter",
    "build_action_metadata",
    # action_param_models
    "ActionLogOption",
    "AllActionParams",
    "BaseActionParams",
    "BiliReserveParams",
    "BUILTIN_ACTION_PARAMS_MAP",
    "ClickParams",
    "CompositeParams",
    "FetchExternalDataParams",
    "GetTextParams",
    "GetWindowParams",
    "HoverParams",
    "IfElseParams",
    "InputParams",
    "LLMParams",
    "LoopParams",
    "MarkResourceResultParams",
    "MouseActionParams",
    "NavigateParams",
    "NewPageParams",
    "PickLineParams",
    "Position",
    "PrintParams",
    "RandomPickParams",
    "ScreenshotParams",
    "ScrollParams",
    "SelectorActionParams",
    "ShuffleListParams",
    "SortListParams",
    "WaitParams",
    # action_result_models
    "AllActionResult",
    "BiliReserveResult",
    "BUILTIN_ACTION_RESULT_MAP",
    "ClickResult",
    "CompositeResult",
    "FetchExternalDataResult",
    "GetTextResult",
    "GetWindowResult",
    "HoverResult",
    "IfElseResult",
    "InputResult",
    "LLMResult",
    "LoopResult",
    "MarkResourceResultResult",
    "NavigateResult",
    "NewPageResult",
    "PickLineResult",
    "PrintResult",
    "RandomPickResult",
    "ScreenshotResult",
    "ScrollResult",
    "ShuffleListResult",
    "SortListResult",
    "WaitResult",
    # workflow_step_models
    "BaseWorkflowStep",
    "BiliReserveWorkflowStep",
    "ClickWorkflowStep",
    "CompositeWorkflowStep",
    "FetchExternalDataWorkflowStep",
    "GetTextWorkflowStep",
    "GetWindowWorkflowStep",
    "HoverWorkflowStep",
    "IfElseWorkflowStep",
    "InputWorkflowStep",
    "LLMWorkflowStep",
    "LoopWorkflowStep",
    "MarkResourceResultWorkflowStep",
    "NavigateWorkflowStep",
    "NewPageWorkflowStep",
    "PickLineWorkflowStep",
    "PrintWorkflowStep",
    "RandomPickWorkflowStep",
    "ScreenshotWorkflowStep",
    "ScrollWorkflowStep",
    "ShuffleListWorkflowStep",
    "SortListWorkflowStep",
    "WaitWorkflowStep",
    "WorkflowStep",
    "_ensure_action_type",
    "_fill_action_type_impl",
    "_WORKFLOW_STEP_CLASS_MAP",
    "create_workflow_step",
    "is_composite_action_id",
    "normalize_composite_action_id",
    "workflow_step_adapter",
]

# 泛型步骤参数 TypeVar（BaseWorkflowStep[P] 使用）
P = TypeVar("P")
