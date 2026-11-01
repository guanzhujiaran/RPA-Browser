"""Workflow 模块 - 工作流请求/响应模型（兼容入口）

定义工作流相关的 API 请求/响应模型（非数据库表模型；DB 表模型见
``app.models.database.workflow.models``）。

原 1141 行单文件已按职责拆分为：

==============================  ============================================
模块                            职责
==============================  ============================================
``workflow/enums.py``           筛选/排序枚举 + ``UpstreamSyncFlag``
``workflow/mixins.py``          ``UpstreamSyncFields``（上游同步 5 字段混入）
``workflow/step_models.py``     工作流步骤请求/响应
``workflow/workflow_requests.py``   工作流请求（CRUD/调度/Fork/资源记录）
``workflow/workflow_responses.py``  工作流响应（详情/列表/运行记录）
``workflow/composite_action_models.py`` 自定义（复合）操作请求/响应
``workflow/action_execution_models.py`` 操作执行/预览/验证/单步执行
``workflow/system_models.py``   系统级模型（参数元数据/重载）
==============================  ============================================

本文件仅做**全量 re-export**，保证
``from app.models.workflow.models import X`` 既有引用零改动；
新代码请直接从上述子模块导入。
"""

from app.models.workflow.action_execution_models import (
    ActionExecuteRequest,
    ActionPreviewRequest,
    ActionPreviewResponse,
    ActionValidateRequest,
    ActionValidateResponse,
    ActionResultResponse,
    ExecuteStepRequest,
    ExecuteStepResponse,
    StepPreviewItem,
    WorkflowStepExecuteRequest,
    WorkflowStepExecuteResponse,
)
from app.models.workflow.composite_action_models import (
    ActionForkRequest,
    ActionForkResponse,
    ActionSyncRequest,
    ActionSyncResponse,
    CompositeActionCreateRequest,
    CompositeActionCreateResponse,
    CompositeActionDeleteRequest,
    CompositeActionDetailResponse,
    CompositeActionGetRequest,
    CompositeActionListItemResponse,
    CompositeActionListRequest,
    CompositeActionUpdateRequest,
    InputVarDefinition,
    NameSearchRequest,
    TagSearchRequest,
    TagWithCount,
)
from app.models.workflow.enums import (
    FilterType,
    SortBy,
    SortOrder,
    UpstreamSyncFlag,
)
from app.models.workflow.mixins import UpstreamSyncFields
from app.models.workflow.step_models import (
    WorkflowStepRequest,
    WorkflowStepResponse,
)
from app.models.workflow.system_models import (
    ActionParameterResponse,
    ReloadActionsResponse,
)
from app.models.workflow.workflow_requests import (
    WorkflowAdminListRequest,
    WorkflowAdminSetEnabledRequest,
    WorkflowCreateRequest,
    WorkflowDuplicateRequest,
    WorkflowExecuteRequest,
    WorkflowForkRequest,
    WorkflowIdRequest,
    WorkflowListRequest,
    WorkflowResourceRecordCreateRequest,
    WorkflowResourceRecordDeleteRequest,
    WorkflowResourceRecordListRequest,
    WorkflowRunDetailRequest,
    WorkflowRunLogListRequest,
    WorkflowRunNowRequest,
    WorkflowRunResumeRequest,
    WorkflowRunStopRequest,
    WorkflowSyncRequest,
    WorkflowUpdateRequest,
)
from app.models.workflow.workflow_responses import (
    WorkflowAdminListItemResponse,
    WorkflowCreateResponse,
    WorkflowDetailResponse,
    WorkflowDuplicateResponse,
    WorkflowExecuteResponse,
    WorkflowForkResponse,
    WorkflowListItemResponse,
    WorkflowResourceRecordDeleteResponse,
    WorkflowResourceRecordItemResponse,
    WorkflowRunLogDetailResponse,
    WorkflowRunLogItemResponse,
    WorkflowRunStopResponse,
    WorkflowSyncResponse,
)

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
    # 工作流调度外壳：立即运行 / 运行记录
    "WorkflowRunNowRequest",
    "WorkflowRunLogListRequest",
    "WorkflowRunLogItemResponse",
    "WorkflowRunLogDetailResponse",
    # 管理端（root）工作流
    "WorkflowAdminListRequest",
    "WorkflowAdminListItemResponse",
    "WorkflowAdminSetEnabledRequest",
    # 复合操作
    "CompositeActionCreateRequest",
    "CompositeActionUpdateRequest",
    "CompositeActionListRequest",
    "CompositeActionDetailResponse",
    "CompositeActionListItemResponse",
    "CompositeActionCreateResponse",
    "CompositeActionGetRequest",
    "CompositeActionDeleteRequest",
    "ActionForkRequest",
    "ActionForkResponse",
    "TagSearchRequest",
    "TagWithCount",
    "NameSearchRequest",
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
    # 已处理资源记录
    "WorkflowResourceRecordListRequest",
    "WorkflowResourceRecordCreateRequest",
    "WorkflowResourceRecordDeleteRequest",
    "WorkflowResourceRecordItemResponse",
    "WorkflowResourceRecordDeleteResponse",
    # 内部值对象 / 混入
    "UpstreamSyncFlag",
    "UpstreamSyncFields",
]
