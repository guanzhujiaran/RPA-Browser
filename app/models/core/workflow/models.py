"""
Core 模块 - 工作流数据库模型

定义工作流、自定义操作、用户插件等数据库模型。
"""

import json
from sqlmodel import SQLModel, Field
from typing import Any, Dict, List, Optional, Type, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid
from playwright.async_api import Page, BrowserContext


# ============ 枚举定义 ============

class ActionType(str, Enum):
    """操作类型"""
    NAVIGATION = "navigation"
    CLICK = "click"
    INPUT = "input"
    SCROLL = "scroll"
    HOVER = "hover"
    WAIT = "wait"
    SCREENSHOT = "screenshot"
    EVALUATE = "evaluate"
    SELECT = "select"
    KEYBOARD = "keyboard"
    MOUSE = "mouse"
    LLM = "llm"
    CUSTOM = "custom"


class ActionTypeEnum(str, Enum):
    """操作类型（数据库用，兼容旧代码）"""
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


# ============ 执行相关模型 ============

class ActionParameter(SQLModel):
    """操作参数定义"""
    name: str = Field(description="参数名称")
    type: str = Field(default="str", description="参数类型")
    required: bool = Field(default=True, description="是否必需")
    default: Any = Field(default=None, description="默认值")
    description: str = Field(default="", description="参数描述")


class ActionMetadata(SQLModel):
    """操作元数据"""
    id: str = Field(description="操作ID")
    name: str = Field(description="操作名称")
    type: str = Field(description="操作类型")
    description: str = Field(default="", description="操作描述")
    parameters: List[ActionParameter] = Field(default_factory=list, description="参数列表")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False, description="错误时重试")
    retry_times: int = Field(default=0, description="重试次数")
    retry_delay: float = Field(default=1.0, description="重试延迟(秒)")
    requires_browser: bool = Field(default=True, description="是否需要浏览器上下文")


class ActionResult(SQLModel):
    """操作执行结果"""
    success: bool = Field(description="是否成功")
    data: Any = Field(default=None, description="返回数据")
    error: Optional[str] = Field(default=None, description="错误信息")
    execution_time: float = Field(default=0.0, description="执行时间(秒)")
    action_id: str = Field(default="", description="操作ID")
    action_name: str = Field(default="", description="操作名称")


@dataclass
class ActionContext:
    """操作执行上下文（包含运行时对象，使用 dataclass）"""
    session_id: str
    browser_id: str
    page: Page  # Playwright Page 对象
    browser: BrowserContext  # Playwright BrowserContext 对象
    params: Dict[str, Any] = field(default_factory=dict)
    user_data: Dict[str, Any] = field(default_factory=dict)


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
        try:
            return json.loads(self.parameters_schema)
        except:
            return []

    def set_parameters_schema(self, schema: List[Dict[str, Any]]):
        self.parameters_schema = json.dumps(schema, ensure_ascii=False)

    def get_steps(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self.steps)
        except:
            return []

    def set_steps(self, steps: List[Dict[str, Any]]):
        self.steps = json.dumps(steps, ensure_ascii=False)

    def get_tags(self) -> List[str]:
        try:
            return json.loads(self.tags)
        except:
            return []

    def set_tags(self, tags: List[str]):
        self.tags = json.dumps(tags, ensure_ascii=False)

    def get_user_data(self) -> Dict[str, Any]:
        try:
            return json.loads(self.user_data) if self.user_data else {}
        except:
            return {}

    def set_user_data(self, data: Dict[str, Any]):
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
        try:
            return json.loads(self.hooks)
        except:
            return []

    def set_hooks(self, hooks: List[str]):
        self.hooks = json.dumps(hooks, ensure_ascii=False)

    def get_config_schema(self) -> Dict[str, Any]:
        try:
            return json.loads(self.config_schema)
        except:
            return {}

    def set_config_schema(self, schema: Dict[str, Any]):
        self.config_schema = json.dumps(schema, ensure_ascii=False)

    def get_default_config(self) -> Dict[str, Any]:
        try:
            return json.loads(self.default_config)
        except:
            return {}

    def set_default_config(self, config: Dict[str, Any]):
        self.default_config = json.dumps(config, ensure_ascii=False)

    def get_user_data(self) -> Dict[str, Any]:
        try:
            return json.loads(self.user_data) if self.user_data else {}
        except:
            return {}

    def set_user_data(self, data: Dict[str, Any]):
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
        try:
            return json.loads(self.steps)
        except:
            return []

    def set_steps(self, steps: List[Dict[str, Any]]):
        self.steps = json.dumps(steps, ensure_ascii=False)

    def get_tags(self) -> List[str]:
        try:
            return json.loads(self.tags)
        except:
            return []

    def set_tags(self, tags: List[str]):
        self.tags = json.dumps(tags, ensure_ascii=False)

    def get_trigger_config(self) -> Dict[str, Any]:
        try:
            return json.loads(self.trigger_config)
        except:
            return {}

    def set_trigger_config(self, config: Dict[str, Any]):
        self.trigger_config = json.dumps(config, ensure_ascii=False)

    def get_user_data(self) -> Dict[str, Any]:
        try:
            return json.loads(self.user_data) if self.user_data else {}
        except:
            return {}

    def set_user_data(self, data: Dict[str, Any]):
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
        try:
            return json.loads(self.results)
        except:
            return []

    def set_results(self, results: List[Dict[str, Any]]):
        self.results = json.dumps(results, ensure_ascii=False)

    def get_user_data(self) -> Dict[str, Any]:
        try:
            return json.loads(self.user_data) if self.user_data else {}
        except:
            return {}

    def set_user_data(self, data: Dict[str, Any]):
        self.user_data = json.dumps(data, ensure_ascii=False) if data else None


__all__ = [
    # 枚举
    "ActionType",
    "ActionTypeEnum",
    "ErrorHandlingEnum",
    "PluginHookEnum",
    # 执行相关模型
    "ActionParameter",
    "ActionMetadata",
    "ActionResult",
    "ActionContext",
    # 数据库模型
    "WorkflowStepModel",
    "CustomActionModel",
    "UserPluginModel",
    "UserWorkflowModel",
    "WorkflowExecutionLogModel",
]
