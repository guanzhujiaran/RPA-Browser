"""
Core 模块 - 工作流数据库模型

定义工作流、自定义操作、用户插件等数据库模型。
"""
from typing import Any, Dict, List, Optional,Generic
from datetime import datetime
import uuid
from pydantic.types import T
from sqlalchemy import Column, JSON, Index
from sqlmodel import SQLModel, Field
from dataclasses import dataclass
from playwright.async_api import Page, BrowserContext
from enum import StrEnum, IntEnum
# ============ 枚举定义 ============

class ActionType(StrEnum):
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
    LOOP = "loop"
    IF_ELSE = "if_else"
    CUSTOM = "custom"
    COMPOSITE = "composite"  # 组合动作（用户自定义的动作序列）


class ErrorHandlingEnum(StrEnum):
    """错误处理策略"""
    STOP = "stop"
    CONTINUE = "continue"
    ROLLBACK = "rollback"


class PluginHookEnum(StrEnum):
    """插件钩子类型"""
    BEFORE_ACTION = "before_action"
    AFTER_ACTION = "after_action"
    ON_SUCCESS = "on_success"
    ON_ERROR = "on_error"
    ON_TIMEOUT = "on_timeout"


class ResourceType(IntEnum):
    """资源类型枚举 - 用于点赞和举报的目标类型"""
    CUSTOM_ACTION = 1
    USER_WORKFLOW = 2
    USER_PLUGIN = 3


class ReportReason(IntEnum):
    """举报理由枚举"""
    SPAM = 1              # 垃圾信息
    INAPPROPRIATE = 2     # 不当内容
    VIOLATION = 3         # 违反规定
    PLAGIARISM = 4        # 抄袭
    OTHER = 5             # 其他


# ============ 执行相关模型 ============

class ActionParameter(SQLModel):
    """操作参数定义（内部使用）"""
    name: str = Field(description="参数名称")
    json_schema: dict[str, Any] = Field(description="完整的 JSON Schema，包含类型、验证规则、嵌套结构等所有信息")


class ActionMetadata(SQLModel):
    """操作元数据（内部使用，包含完整信息）"""
    id: str = Field(description="操作ID")
    name: str = Field(description="操作名称")
    type: str = Field(description="操作类型")
    description: str = Field(default="", description="操作描述")
    parameters: List[ActionParameter] = Field(default_factory=list, description="参数列表")
    json_schema: dict[str, Any] | None = Field(default=None, description="完整的 JSON Schema 定义（包含 $defs），用于前端解析 $ref 引用")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False, description="错误时重试")
    retry_times: int = Field(default=0, description="重试次数")
    retry_delay: float = Field(default=1.0, description="重试延迟(秒)")
    requires_browser: bool = Field(default=True, description="是否需要浏览器上下文")


class ActionMetadataResponse(SQLModel):
    """操作元数据响应（API 返回，精简版）"""
    action_id: str
    json_schema: dict[str, Any]


class ActionResult(SQLModel,Generic[T]):
    """操作执行结果"""
    success: bool = Field(description="是否成功")
    data: T = Field(default=None, description="返回数据")
    error: Optional[str] = Field(default=None, description="错误信息")
    execution_time: float = Field(default=0.0, description="执行时间(秒)")
    action_id: str = Field(default="", description="操作ID")
    action_name: str = Field(default="", description="操作名称")
    logs: List[str] = Field(default_factory=list, description="执行过程中的日志记录")


@dataclass
class ActionContext:
    """操作执行上下文（包含运行时对象，使用 dataclass）"""
    session_id: str
    browser_id: str
    page: "Page"  # Playwright Page 对象 (使用字符串注解避免循环导入)
    browser: "BrowserContext"  # Playwright BrowserContext 对象
    params: Dict[str, Any] = Field(default_factory=dict)
    user_data: Dict[str, Any] = Field(default_factory=dict)


# ============ 数据库模型 ============

class WorkflowStep(SQLModel):
    """
    工作流步骤定义（运行时模型，支持嵌套）
    """
    action_id: str = Field(description="操作ID，对应预测注册action_id和自定义操作id")
    params: Dict[str, Any] = Field(default_factory=dict, description="参数字典，支持 {{变量名}} 模板")
    children: Optional[List['WorkflowStep']] = Field(default=None, description="子步骤列表（用于循环体或分支）")
    condition: Optional[str] = Field(default=None, description="执行条件表达式（如：state.loop.index < 5）")
    output_var: Optional[str] = Field(default=None, description="将结果存入 state.variables 的键名")
    loop_count: Optional[int] = Field(default=None, description="固定循环次数")
    loop_while: Optional[str] = Field(default=None, description="条件循环表达式")
    loop_until: Optional[str] = Field(default=None, description="条件退出表达式")
    retry: int = Field(default=0, description="失败重试次数")
    user_data: Optional[Dict[str, Any]] = Field(default=None, description="步骤级自定义数据")


class CustomAction(SQLModel, table=True):
    """
    自定义组合动作表
    
    用户定义的、可复用的动作组合（类似函数）。
    包含多个步骤（steps），可以被 Workflow 引用和调用。
    
    与 Workflow 的区别：
    - CustomAction: 轻量级、可复用、有明确输入输出参数的动作组合
    - Workflow: 完整的业务流程，支持复杂的控制流、错误处理等
    
    概念层次：
    1. 原子动作 (Atomic Actions) - 系统预注册，如 Click, Input, Navigate
    2. 组合动作 (Composite Actions) - 用户定义的步骤序列（本模型）
    3. 工作流 (Workflows) - 完整的业务流程，可调用原子动作和组合动作
    """
    __tablename__ = "custom_action"
    __table_args__ = (
        # 同一用户下名称必须唯一（类似 GitHub 仓库命名）
        Index('idx_user_action_name_unique', 'mid', 'name', unique=True),
    )

    id: int | None = Field(default=None, primary_key=True,)
    action_id: str = Field(
        index=True, 
        unique=True, 
        max_length=100, 
        description="操作唯一标识（系统自动生成，格式：ca_xxx，用户不可修改）"
    )
    name: str = Field(max_length=200, description="显示名称（用户可编辑的业务名称）")
    version: str = Field(default="1.0.0", max_length=50)
    action_type: ActionType = Field(
        default=ActionType.COMPOSITE,
        description="操作类型（组合动作）"
    )
    parameters_schema: List[Dict[str, Any]] = Field(
        default_factory=list, 
        sa_column=Column(JSON), 
        description="参数定义JSON（定义此组合动作的输入参数）"
    )
    steps: List[Dict[str, Any]] = Field(
        default_factory=list, 
        sa_column=Column(JSON), 
        description="步骤列表JSON（引用原子动作或其他组合动作的执行序列）"
    )
    is_composite: bool = Field(default=True, description="是否为组合动作")
    description: str = Field(default="", max_length=500, description="动作描述")
    author: str = Field(default="", max_length=100)
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON), description="标签JSON数组")
    user_data: Dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="自定义数据JSON")
    mid: int = Field(index=True, description="用户ID")
    is_enabled: bool = Field(default=True)
    is_public: bool = Field(default=False, description="是否公开给所有用户")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False)
    retry_times: int = Field(default=0)
    retry_delay: float = Field(default=1.0)
    likes_count: int = Field(default=0, description="点赞数")
    reports_count: int = Field(default=0, description="举报数")
    is_verified: bool = Field(default=False, description="是否经过官方验证")
    forks_count: int = Field(default=0, description="被 Fork 次数")
    forked_from_id: int | None = Field(
        default=None,
        foreign_key="custom_action.id",
        description="Fork 来源的操作ID"
    )
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class WorkflowPluginRelation(SQLModel, table=True):
    """
    工作流插件关联表（多对多）
    用于存储工作流引用的插件及其特定配置参数
    """
    __tablename__ = "workflow_plugin_relation"

    id: int | None = Field(default=None, primary_key=True,)
    workflow_id: str = Field(foreign_key="user_workflow.workflow_id", index=True, description="关联的工作流ID")
    plugin_id: str = Field(foreign_key="user_plugin.plugin_id", index=True, description="关联的插件ID")
    config_params: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="该工作流下此插件的特定配置参数")


class ActionPluginRelation(SQLModel, table=True):
    """
    动作插件关联表（多对多）
    自定义动作与插件的多对多关联表
    """
    __tablename__ = "action_plugin_relation"

    id: int | None = Field(default=None, primary_key=True,)
    action_id: str = Field(foreign_key="custom_action.action_id", index=True, description="关联的动作ID")
    plugin_id: str = Field(foreign_key="user_plugin.plugin_id", index=True, description="关联的插件ID")
    config_params: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="该动作下此插件的特定配置参数")


class UserPlugin(SQLModel, table=True):
    """
    用户插件表（挂载机制）
    用于在特定的生命周期钩子处自动插入并执行自定义动作。
    """
    __tablename__ = "user_plugin"
    __table_args__ = (
        # 同一用户下名称必须唯一（类似 GitHub 仓库命名）
        Index('idx_user_plugin_name_unique', 'mid', 'name', unique=True),
    )

    id: int | None = Field(default=None, primary_key=True,)
    plugin_id: str = Field(index=True, unique=True, max_length=100, description="插件唯一标识")
    name: str = Field(max_length=200, description="插件名称")
    
    # 核心逻辑：关联到具体的自定义动作
    custom_action_id: str = Field(
        max_length=100, 
        description="要执行的自定义动作ID (关联 CustomAction.action_id)"
    )
    
    # 钩子类型：决定在什么时候执行
    hook_type: str = Field(
        max_length=50, 
        description="钩子类型: before_action, after_action, on_error, on_success"
    )
    
    description: str = Field(default="", max_length=500)
    mid: int = Field(index=True, description="用户ID")
    is_enabled: bool = Field(default=True)
    is_public: bool = Field(default=False, description="是否公开给所有用户")
    priority: int = Field(default=100, description="执行优先级，数值越小越先执行")
    likes_count: int = Field(default=0, description="点赞数")
    reports_count: int = Field(default=0, description="举报数")
    is_verified: bool = Field(default=False, description="是否经过官方验证")
    forks_count: int = Field(default=0, description="被 Fork 次数")
    forked_from_id: int | None = Field(
        default=None,
        foreign_key="user_plugin.id",
        description="Fork 来源的插件ID"
    )
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class UserWorkflow(SQLModel, table=True):
    """
    用户工作流表
    用户定义的工作流，包含多个步骤。
    workflow_id 自动生成 UUID。
    """
    __tablename__ = "user_workflow"
    __table_args__ = (
        # 同一用户下名称必须唯一（类似 GitHub 仓库命名）
        Index('idx_user_workflow_name_unique', 'mid', 'name', unique=True),
    )

    id: int | None = Field(primary_key=True,)
    workflow_id: uuid.UUID = Field(
        index=True,
        unique=True,  # 外键引用需要唯一性
        max_length=100,
        default="",
        description="工作流唯一标识，自动生成UUID"
    )
    name: str = Field(max_length=200, description="显示名称，支持重命名")
    version: str = Field(default="1.0.0", max_length=50)
    steps: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON), description="步骤列表JSON")
    on_error: str = Field(default="stop", max_length=50)
    description: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=100)
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    user_data: Dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="自定义数据JSON，支持工作流级变量")
    mid: int = Field(index=True)
    is_enabled: bool = Field(default=True)
    is_public: bool = Field(default=False, description="是否公开给所有用户")
    trigger_type: str = Field(default="manual", max_length=50)
    trigger_config: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # 移除了 enabled_plugins JSON 字段，改用 WorkflowPluginRelation 关联表
    likes_count: int = Field(default=0, description="点赞数")
    reports_count: int = Field(default=0, description="举报数")
    is_verified: bool = Field(default=False, description="是否经过官方验证")
    forks_count: int = Field(default=0, description="被 Fork 次数")
    forked_from_id: int | None = Field(
        default=None,
        foreign_key="user_workflow.id",
        description="Fork 来源的工作流ID"
    )
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class WorkflowExecutionLog(SQLModel, table=True):
    """工作流执行日志表"""
    __tablename__ = "workflow_execution_log"

    id: int | None = Field(primary_key=True,)
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
    results: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    user_data: Dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="执行时的自定义数据")
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = Field(default=None)


# ============ 社区互动中间表 ============

class ResourceLike(SQLModel, table=True):
    """
    资源点赞表 - 存储用户对资源的点赞记录
    
    使用中间表而非直接在资源表中存储用户列表的优势：
    1. 支持高效的用户级操作（点赞/取消点赞）
    2. 支持查询用户的点赞历史
    3. 避免资源表字段膨胀
    """
    __tablename__ = "resource_like"
    __table_args__ = (
        # 同一用户对同一资源只能点赞一次
        Index('idx_unique_like', 'mid', 'resource_type', 'resource_id', unique=True),
    )

    id: int | None = Field(default=None, primary_key=True,)
    mid: int = Field(index=True, description="点赞用户ID")
    resource_type: int = Field(index=True, description="资源类型（1=自定义操作, 2=工作流, 3=插件）")
    resource_id: int = Field(index=True, description="资源ID（对应具体资源表的主键）")
    created_at: datetime = Field(default_factory=datetime.now)


class ResourceReport(SQLModel, table=True):
    """
    资源举报表 - 存储用户对资源的举报记录
    
    举报默认生效，管理员可以标记为无效举报，此时：
    1. 被举报资源的举报数减少
    2. 该举报记录标记为无效
    """
    __tablename__ = "resource_report"
    __table_args__ = (
        # 同一用户对同一资源在短时间内只能举报一次（防刷）
        Index('idx_unique_report', 'mid', 'resource_type', 'resource_id'),
    )

    id: int | None = Field(primary_key=True,)
    mid: int = Field(index=True, description="举报用户ID")
    resource_type: int = Field(index=True, description="资源类型（1=自定义操作, 2=工作流, 3=插件）")
    resource_id: int = Field(index=True, description="资源ID（对应具体资源表的主键）")
    reason: int = Field(description="举报理由（1=垃圾信息, 2=不当内容, 3=违反规定, 4=抄袭, 5=其他）")
    description: str = Field(default="", max_length=500, description="详细描述")
    is_valid: bool = Field(default=True, description="是否为有效举报（管理员可修改）")
    reviewed_by_mid: int | None = Field(default=None, description="审核管理员ID")
    reviewed_at: datetime | None = Field(default=None, description="审核时间")
    created_at: datetime = Field(default_factory=datetime.now)


__all__ = [
    # 枚举
    "ActionType",
    "ErrorHandlingEnum",
    "PluginHookEnum",
    "ResourceType",
    "ReportReason",
    # 执行相关模型
    "ActionParameter",
    "ActionMetadata",
    "ActionResult",
    "ActionContext",
    # 数据库模型
    "WorkflowStep",
    "CustomAction",
    "WorkflowPluginRelation",
    "ActionPluginRelation",
    "UserPlugin",
    "UserWorkflow",
    "WorkflowExecutionLog",
    "ResourceLike",
    "ResourceReport",
    "ActionMetadataResponse"
]
