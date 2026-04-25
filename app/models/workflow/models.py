"""
Workflow 模块 - 工作流数据库模型

定义工作流、自定义操作、用户插件等数据库模型。
"""

from sqlmodel import SQLModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime
from enum import Enum
import uuid


# ============ 枚举定义 ============

class ActionTypeEnum(str, Enum):
    """操作类型"""
    NAVIGATION = "navigation"
    CLICK = "click"
    INPUT = "input"
    SCROLL = "scroll"
    WAIT = "wait"
    SCREENSHOT = "screenshot"
    EVALUATE = "evaluate"
    LLM = "llm"
    CUSTOM = "custom"


class ErrorHandlingEnum(str, Enum):
    """错误处理策略"""
    STOP = "stop"
    CONTINUE = "continue"
    ROLLBACK = "rollback"


class PluginHookEnum(str, Enum):
    """插件钩子类型"""
    BEFORE_ACTION = "before_action"
    AFTER_ACTION = "after_action"
    ON_SUCCESS = "on_success"
    ON_ERROR = "on_error"
    ON_TIMEOUT = "on_timeout"


# ============ 数据库模型 ============

class WorkflowStepModel(SQLModel, table=True):
    """
    工作流步骤模型
    定义工作流中的单个步骤配置。
    """
    __tablename__ = "workflow_step"

    id: int | None = Field(default=None, primary_key=True)
    workflow_id: str = Field(index=True, max_length=100)
    step_index: int = Field(default=0, description="步骤索引")
    action_id: str = Field(max_length=100, description="操作ID")
    params: str = Field(default="{}", description="参数字典JSON，支持 {{变量名}} 模板")
    loop_count: int | None = Field(default=None, description="循环次数")
    loop_while: str | None = Field(default=None, description="条件循环表达式")
    loop_until: str | None = Field(default=None, description="条件退出表达式")
    retry: int = Field(default=0, description="失败重试次数")
    condition: str | None = Field(default=None, description="执行条件表达式")
    user_data: str | None = Field(default=None, description="自定义数据JSON，步骤级变量")


class CustomActionModel(SQLModel, table=True):
    """
    自定义操作模型
    用户自定义的组合操作，可以包含多个步骤。
    """
    __tablename__ = "custom_action"

    id: int | None = Field(default=None, primary_key=True)
    action_id: str = Field(index=True, max_length=100, description="操作唯一标识")
    name: str = Field(max_length=200, description="显示名称")
    version: str = Field(default="1.0.0", max_length=50)
    action_type: str = Field(max_length=50, description="操作类型: composite, code")
    parameters_schema: str = Field(default="[]", description="参数定义JSON")
    steps: str = Field(default="[]", description="步骤列表JSON")
    is_composite: bool = Field(default=True, description="是否为组合操作")
    code: str | None = Field(default=None, description="自定义代码")
    description: str = Field(default="", max_length=500, description="操作描述")
    author: str = Field(default="", max_length=100)
    tags: str = Field(default="[]", description="标签JSON数组")
    user_data: str | None = Field(default=None, description="自定义数据JSON")
    mid: int = Field(index=True, description="用户ID")
    is_enabled: bool = Field(default=True)
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False)
    retry_times: int = Field(default=0)
    retry_delay: float = Field(default=1.0)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def get_parameters_schema(self) -> List[Dict[str, Any]]:
        import json
        try:
            return json.loads(self.parameters_schema)
        except:
            return []

    def set_parameters_schema(self, schema: List[Dict[str, Any]]):
        import json
        self.parameters_schema = json.dumps(schema, ensure_ascii=False)

    def get_steps(self) -> List[Dict[str, Any]]:
        import json
        try:
            return json.loads(self.steps)
        except:
            return []

    def set_steps(self, steps: List[Dict[str, Any]]):
        import json
        self.steps = json.dumps(steps, ensure_ascii=False)

    def get_tags(self) -> List[str]:
        import json
        try:
            return json.loads(self.tags)
        except:
            return []

    def set_tags(self, tags: List[str]):
        import json
        self.tags = json.dumps(tags, ensure_ascii=False)

    def get_user_data(self) -> Dict[str, Any]:
        import json
        try:
            return json.loads(self.user_data) if self.user_data else {}
        except:
            return {}

    def set_user_data(self, data: Dict[str, Any]):
        import json
        self.user_data = json.dumps(data, ensure_ascii=False)


class UserPluginModel(SQLModel, table=True):
    """
    用户插件模型
    用户自定义的插件，可以在操作执行时插入钩子逻辑。
    """
    __tablename__ = "user_plugin"

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: str = Field(index=True, max_length=100)
    name: str = Field(max_length=200)
    version: str = Field(default="1.0.0", max_length=50)
    hooks: str = Field(default="[]", description="钩子列表JSON")
    code: str | None = Field(default=None)
    description: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=100)
    config_schema: str = Field(default="{}", description="配置定义JSON")
    default_config: str = Field(default="{}", description="默认配置JSON")
    user_data: str | None = Field(default=None, description="自定义数据JSON")
    mid: int = Field(index=True)
    is_enabled: bool = Field(default=True)
    priority: int = Field(default=100)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def get_hooks(self) -> List[str]:
        import json
        try:
            return json.loads(self.hooks)
        except:
            return []

    def set_hooks(self, hooks: List[str]):
        import json
        self.hooks = json.dumps(hooks, ensure_ascii=False)

    def get_config_schema(self) -> Dict[str, Any]:
        import json
        try:
            return json.loads(self.config_schema)
        except:
            return {}

    def set_config_schema(self, schema: Dict[str, Any]):
        import json
        self.config_schema = json.dumps(schema, ensure_ascii=False)

    def get_default_config(self) -> Dict[str, Any]:
        import json
        try:
            return json.loads(self.default_config)
        except:
            return {}

    def set_default_config(self, config: Dict[str, Any]):
        import json
        self.default_config = json.dumps(config, ensure_ascii=False)

    def get_user_data(self) -> Dict[str, Any]:
        import json
        try:
            return json.loads(self.user_data) if self.user_data else {}
        except:
            return {}

    def set_user_data(self, data: Dict[str, Any]):
        import json
        self.user_data = json.dumps(data, ensure_ascii=False) if data else None


class UserWorkflowModel(SQLModel, table=True):
    """
    用户工作流模型
    用户定义的工作流，包含多个步骤。
    workflow_id 自动生成 UUID。
    """
    __tablename__ = "user_workflow"

    id: int | None = Field(default=None, primary_key=True)
    workflow_id: str = Field(
        index=True,
        max_length=100,
        default="",
        description="工作流唯一标识，自动生成UUID"
    )
    name: str = Field(max_length=200, description="显示名称，支持重命名")
    version: str = Field(default="1.0.0", max_length=50)
    steps: str = Field(default="[]", description="步骤列表JSON")
    on_error: str = Field(default="stop", max_length=50)
    description: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=100)
    tags: str = Field(default="[]")
    user_data: str | None = Field(default=None, description="自定义数据JSON，支持工作流级变量")
    mid: int = Field(index=True)
    is_enabled: bool = Field(default=True)
    trigger_type: str = Field(default="manual", max_length=50)
    trigger_config: str = Field(default="{}")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 自动生成 workflow_id
        if not self.workflow_id:
            self.workflow_id = str(uuid.uuid4())

    def get_steps(self) -> List[Dict[str, Any]]:
        import json
        try:
            return json.loads(self.steps)
        except:
            return []

    def set_steps(self, steps: List[Dict[str, Any]]):
        import json
        self.steps = json.dumps(steps, ensure_ascii=False)

    def get_tags(self) -> List[str]:
        import json
        try:
            return json.loads(self.tags)
        except:
            return []

    def set_tags(self, tags: List[str]):
        import json
        self.tags = json.dumps(tags, ensure_ascii=False)

    def get_trigger_config(self) -> Dict[str, Any]:
        import json
        try:
            return json.loads(self.trigger_config)
        except:
            return {}

    def set_trigger_config(self, config: Dict[str, Any]):
        import json
        self.trigger_config = json.dumps(config, ensure_ascii=False)

    def get_user_data(self) -> Dict[str, Any]:
        import json
        try:
            return json.loads(self.user_data) if self.user_data else {}
        except:
            return {}

    def set_user_data(self, data: Dict[str, Any]):
        import json
        self.user_data = json.dumps(data, ensure_ascii=False) if data else None


class WorkflowExecutionLogModel(SQLModel, table=True):
    """工作流执行日志"""
    __tablename__ = "workflow_execution_log"

    id: int | None = Field(default=None, primary_key=True)
    workflow_id: str = Field(index=True, max_length=100)
    session_id: str = Field(index=True, max_length=100)
    browser_id: str = Field(index=True, max_length=100)
    mid: int = Field(index=True)
    execution_id: str = Field(index=True, max_length=100)
    status: str = Field(max_length=50)
    total_time: float = Field(default=0.0)
    steps_count: int = Field(default=0)
    success_count: int = Field(default=0)
    failed_count: int = Field(default=0)
    results: str = Field(default="[]")
    user_data: str | None = Field(default=None, description="执行时的自定义数据")
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = Field(default=None)

    def get_results(self) -> List[Dict[str, Any]]:
        import json
        try:
            return json.loads(self.results)
        except:
            return []

    def set_results(self, results: List[Dict[str, Any]]):
        import json
        self.results = json.dumps(results, ensure_ascii=False)

    def get_user_data(self) -> Dict[str, Any]:
        import json
        try:
            return json.loads(self.user_data) if self.user_data else {}
        except:
            return {}

    def set_user_data(self, data: Dict[str, Any]):
        import json
        self.user_data = json.dumps(data, ensure_ascii=False) if data else None


# ============ 请求/响应模型 ============

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
    plugin_ids: List[str] = Field(default_factory=list, description="启用的插件ID")
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
    plugin_ids: List[str] = Field(default_factory=list)


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
    plugin_ids: List[str] = Field(default_factory=list)


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


# ============ 插件请求/响应 ============

class PluginCreateRequest(SQLModel):
    """创建插件请求"""
    name: str = Field(description="插件名称")
    hooks: List[str] = Field(description="钩子类型列表")
    description: str = Field(default="")
    code: str | None = Field(default=None)
    user_data: Dict[str, Any] | None = Field(default=None)


class PluginUpdateRequest(SQLModel):
    """更新插件请求"""
    id: int = Field(description="插件数据库ID")
    name: str | None = Field(default=None)
    description: str | None = Field(default=None)
    hooks: List[str] | None = Field(default=None)
    code: str | None = Field(default=None)
    user_data: Dict[str, Any] | None = Field(default=None)
    priority: int | None = Field(default=None)
    is_enabled: bool | None = Field(default=None)


class PluginDetailResponse(SQLModel):
    """插件详情响应"""
    id: int
    plugin_id: str
    name: str
    hooks: List[str]
    description: str
    priority: int
    user_data: Dict[str, Any]
    is_enabled: bool
    created_at: datetime | None = None


class PluginListItemResponse(SQLModel):
    """插件列表项响应"""
    id: int
    plugin_id: str
    name: str
    description: str
    is_enabled: bool
    created_at: datetime | None = None


class PluginMetadataResponse(SQLModel):
    """插件元数据响应"""
    id: str
    name: str
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    hooks: List[str] = []
    priority: int = 100
    config_schema: Dict[str, Any] | None = None


class PluginCreateResponse(SQLModel):
    """创建插件响应"""
    id: int
    plugin_id: str


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
    # 数据库模型
    "WorkflowStepModel",
    "CustomActionModel",
    "UserPluginModel",
    "UserWorkflowModel",
    "WorkflowExecutionLogModel",
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
    "PluginCreateRequest",
    "PluginUpdateRequest",
    "PluginDetailResponse",
    "PluginListItemResponse",
    "PluginMetadataResponse",
    "PluginCreateResponse",
    "ActionParameterResponse",
    "ActionMetadataResponse",
    "ReloadActionsResponse",
]
