"""
Core 模块 - 工作流数据库模型

定义工作流、自定义操作、用户插件等数据库模型。
"""
from app.models.execution.action_params import WorkflowStep
from app.models.execution.action_params import BuiltinActionType
from typing import Any, Dict, List, Generic
from datetime import datetime
from pydantic.types import T
from pydantic import field_validator
from sqlalchemy import Column, JSON, Index, String
from sqlmodel import SQLModel, Field
from enum import StrEnum, IntEnum


class TriggerType(StrEnum):
    """触发类型"""
    MANUAL = "manual"          # 手动触发
    SCHEDULED = "scheduled"    # 定时触发
    EVENT = "event"            # 事件触发


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
    """资源类型枚举"""
    CUSTOM_ACTION = 1
    USER_WORKFLOW = 2
    USER_PLUGIN = 3


class ReportReason(IntEnum):
    """举报理由枚举"""
    SPAM = 1
    INAPPROPRIATE = 2
    VIOLATION = 3
    PLAGIARISM = 4
    OTHER = 5


# region ============ 执行相关模型 ============

class ActionParameter(SQLModel):
    """操作参数定义（内部使用）"""
    name: str = Field(description="参数名称")
    json_schema: dict[str, Any] = Field(description="完整的 JSON Schema")


class ActionMetadata(SQLModel):
    """操作元数据（内部使用）"""
    id: BuiltinActionType = Field(description="操作ID")
    name: str = Field(description="操作名称")
    type: BuiltinActionType = Field(description="操作类型")
    description: str = Field(default="", description="操作描述")
    parameters: List[ActionParameter] = Field(
        default_factory=list, description="参数列表")
    json_schema: dict[str, Any] | None = Field(
        default=None, description="完整的 JSON Schema")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False, description="错误时重试")
    retry_times: int = Field(default=0, description="重试次数")
    retry_delay: float = Field(default=1.0, description="重试延迟(秒)")
    requires_browser: bool = Field(default=True, description="是否需要浏览器上下文")


class ActionMetadataResponse(SQLModel):
    """操作元数据响应（API 返回）"""
    action_id: str = Field(description="预设操作ID")
    action_type: BuiltinActionType = Field(description="操作类型")
    json_schema: dict[str, Any] = Field(description="完整的 JSON Schema")


class ActionResult(SQLModel, Generic[T]):
    """操作执行结果"""
    success: bool = Field(description="是否成功")
    data: T = Field(default=None, description="返回数据")
    error: str | None = Field(default=None, description="错误信息")
    execution_time: float = Field(default=0.0, description="执行时间(秒)")
    action_id: str = Field(default="", description="操作ID")
    action_name: str = Field(default="", description="操作名称")
    logs: List[str] = Field(default_factory=list, description="日志记录")

# endregion
# region ============ 社区资源基类 ============


class CommunityResourceBase(SQLModel):
    """社区资源基类 - mid 字段：数据库存储为 str，运行时使用 int"""
    mid: int = Field(sa_type=String, index=True, description="当前所有者用户ID")
    original_mid: int = Field(
        sa_type=String, index=True, description="最初创建者用户ID")
    is_enabled: bool = Field(default=True)
    is_public: bool = Field(default=False, description="是否公开")
    likes_count: int = Field(default=0, description="点赞数")
    reports_count: int = Field(default=0, description="举报数")
    is_verified: bool = Field(default=False, description="是否经过官方验证")
    forks_count: int = Field(default=0, description="被 Fork 次数")
    forked_from_id: int | None = Field(
        default=None, description="Fork 来源的资源ID")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @field_validator("mid", "original_mid", mode="before")
    @classmethod
    def validate_mid(cls, v: Any) -> int:
        """将 str 类型的 mid 转换为 int"""
        if isinstance(v, str):
            return int(v)
        return v

    @field_validator("mid", "original_mid", mode="wrap")
    @classmethod
    def serialize_mid_to_str(cls, v: Any, handler: Any) -> str:
        """将 mid 转换为 str 用于数据库存储"""
        validated = handler(v)
        if isinstance(validated, int):
            return str(validated)
        return validated


class ExecutionStatus(StrEnum):
    """执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ExecutionTask(SQLModel):
    """执行任务"""
    id: str
    session_id: str
    browser_id: str
    status: ExecutionStatus
    actions: List[Dict[str, Any]]
    current_index: int = 0
    results: List[ActionResult] = Field(default_factory=list)
    created_at: float = Field(
        default_factory=lambda: __import__("time").time())
    started_at: float | None = None
    finished_at: float | None = None
    total_time: float = 0.0
    error: str | None = None

# endregion
# region ============ 数据库模型 ============


class CompositeActionModel(CommunityResourceBase, table=True):
    """
    复合动作表

    用户定义的、可复用的动作组合（类似函数）。
    """
    __tablename__ = "composite_action_model"
    __table_args__ = (
        Index('idx_user_action_name_unique', 'mid', 'name', unique=True),
    )

    id: int | None = Field(default=None, primary_key=True,)
    action_id: str = Field(
        index=True,
        unique=True,
        max_length=100,
        description="操作唯一标识（系统自动生成，格式：ca_xxx）"
    )
    name: str = Field(max_length=200, description="显示名称")
    version: str = Field(default="1.0.0", max_length=50)
    action_type: BuiltinActionType = Field(
        default=BuiltinActionType.COMPOSITE,
        description="操作类型"
    )
    parameters_schema: List[Dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="参数定义JSON"
    )
    steps: List[WorkflowStep] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description=(
            "步骤列表，每个元素为 WorkflowStep 结构"
        )
    )
    is_composite: bool = Field(default=True, description="是否为组合动作")
    description: str = Field(default="", max_length=500, description="动作描述")
    author: str = Field(default="", max_length=100)
    tags: List[str] = Field(default_factory=list,
                            sa_column=Column(JSON), description="标签")
    input_vars: List[Dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="输入变量定义"
    )
    output_vars: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="输出变量名称列表"
    )
    forked_from_id: int | None = Field(
        default=None,
        foreign_key="composite_action_model.id",
        description="Fork 来源的操作ID"
    )
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False)
    retry_times: int = Field(default=0)
    retry_delay: float = Field(default=1.0)



class WorkflowPluginRelation(SQLModel, table=True):
    """工作流插件关联表"""
    __tablename__ = "workflow_plugin_relation"

    id: int | None = Field(default=None, primary_key=True,)
    workflow_id: str = Field(
        foreign_key="user_workflow.workflow_id", index=True, description="工作流ID")
    plugin_id: str = Field(
        foreign_key="user_plugin.plugin_id", index=True, description="插件ID")
    config_params: Dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON), description="配置参数")



class UserPlugin(CommunityResourceBase, table=True):
    """用户插件表"""
    __tablename__ = "user_plugin"
    __table_args__ = (
        Index('idx_user_plugin_name_unique', 'mid', 'name', unique=True),
    )

    id: int | None = Field(default=None, primary_key=True,)
    plugin_id: str = Field(index=True, unique=True,
                           max_length=100, description="插件唯一标识")
    name: str = Field(max_length=200, description="插件名称")

    custom_action_id: str = Field(
        max_length=100,
        description="要执行的自定义动作ID"
    )

    hook_type: PluginHookEnum = Field(
        description="钩子类型"
    )

    description: str = Field(default="", max_length=500)
    forked_from_id: int | None = Field(
        default=None,
        foreign_key="user_plugin.id",
        description="Fork 来源的插件ID"
    )
    priority: int = Field(default=100, description="优先级")


class UserWorkflow(CommunityResourceBase, table=True):
    """用户工作流表 - 定时任务调度配置"""
    __tablename__ = "user_workflow"
    __table_args__ = (
        Index('idx_user_workflow_name_unique', 'mid', 'name', unique=True),
    )

    id: int | None = Field(primary_key=True,)
    workflow_id: str = Field(
        index=True,
        unique=True,
        max_length=100,
        default="",
        description="工作流唯一标识"
    )
    name: str = Field(max_length=200, description="显示名称")
    custom_action_id: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="要执行的自定义动作ID"
    )
    description: str = Field(default="", max_length=500)
    forked_from_id: int | None = Field(
        default=None,
        foreign_key="user_workflow.id",
        description="Fork 来源的工作流ID"
    )
    trigger_type: str = Field(
        default="manual", max_length=50, description="触发类型")
    trigger_config: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="触发配置"
    )


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
    results: List[Dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON))
    variables: Dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON), description="变量池快照")
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = Field(default=None)


class ResourceLike(SQLModel, table=True):
    """资源点赞表"""
    __tablename__ = "resource_like"
    __table_args__ = (
        Index('idx_unique_like', 'mid', 'resource_type',
              'resource_id', unique=True),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True,)
    mid: int = Field(index=True, description="点赞用户ID")
    resource_type: ResourceType = Field(index=True, description="资源类型")
    resource_id: int = Field(index=True, description="资源ID")
    created_at: datetime = Field(default_factory=datetime.now)


class ResourceReport(SQLModel, table=True):
    """资源举报表"""
    __tablename__ = "resource_report"
    __table_args__ = (
        Index('idx_unique_report', 'mid', 'resource_type', 'resource_id'),
        {"extend_existing": True},
    )

    id: int | None = Field(primary_key=True,)
    mid: int = Field(index=True, description="举报用户ID")
    resource_type: ResourceType = Field(index=True, description="资源类型")
    resource_id: int = Field(index=True, description="资源ID")
    reason: ReportReason = Field(description="举报理由")
    description: str = Field(default="", max_length=500, description="详细描述")
    is_valid: bool = Field(default=True, description="是否有效")
    reviewed_by_mid: int | None = Field(default=None, description="审核管理员ID")
    reviewed_at: datetime | None = Field(default=None, description="审核时间")
    created_at: datetime = Field(default_factory=datetime.now)


class ActionExecutionLog(SQLModel, table=True):
    """
    动作执行日志表

    记录每一次动作或插件的执行详情。
    使用树形结构追踪执行链路。
    """
    __tablename__ = "action_execution_log"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    execution_id: str = Field(index=True, max_length=100, description="执行批次ID")
    parent_execution_id: str | None = Field(
        default=None,
        max_length=100,
        description="父执行ID（用于树形结构）"
    )
    action_id: str = Field(index=True, max_length=100, description="动作ID")
    action_name: str = Field(default="", max_length=200)
    action_type: BuiltinActionType = Field(description="动作类型")
    status: ExecutionStatus = Field(description="执行状态")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="实际执行参数"
    )
    result_data: Dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="执行结果数据"
    )
    error_message: str | None = Field(default=None, max_length=1000)

    execution_time: float = Field(default=0.0, description="执行时长(秒)")

    depth: int = Field(default=0, description="执行深度（用于追踪嵌套）")
    order: int = Field(default=0, description="同层级执行顺序")

    logs: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="执行日志"
    )

    mid: int = Field(index=True)

    workflow_id: str | None = Field(
        default=None,
        index=True,
        max_length=100,
        description="关联的工作流ID"
    )

    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = Field(default=None)


class WorkflowRecord(SQLModel, table=True):
    """
    工作流记录表 - 简化设计

    核心变化：
    - 只允许一个 action_id 入口
    - 不再支持嵌套的 steps 结构
    - 通过 crontab 表达式支持周期运行
    """
    __tablename__ = "workflow_record"
    __table_args__ = (
        Index('idx_workflow_user_name_unique', 'mid', 'name', unique=True),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    workflow_id: str = Field(
        index=True,
        unique=True,
        max_length=100,
        description="工作流唯一标识"
    )
    name: str = Field(max_length=200, description="显示名称")
    description: str = Field(default="", max_length=500)

    # 单一入口
    entry_action_id: str = Field(
        max_length=100,
        description="入口动作ID（引用 unified_action 表）"
    )

    # 运行时参数模板
    params_template: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="参数模板，支持 {{变量}} 语法"
    )

    # 周期运行配置
    trigger_type: TriggerType = Field(
        default=TriggerType.MANUAL,
        description="触发类型"
    )
    crontab_expression: str | None = Field(
        default=None,
        max_length=100,
        description="Crontab 表达式（如 '0 8 * * *' 表示每天8点）"
    )
    is_scheduled: bool = Field(
        default=False,
        description="是否启用周期调度"
    )

    # 元数据
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    # 输入输出定义
    input: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="输入变量（全局变量）"
    )
    output: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="输出变量名列表"
    )

    # 所有权
    mid: int = Field(index=True)

    # 状态
    is_enabled: bool = Field(default=True)
    is_public: bool = Field(default=False)

    # 社区功能
    likes_count: int = Field(default=0)
    reports_count: int = Field(default=0)
    is_verified: bool = Field(default=False)
    forks_count: int = Field(default=0)
    forked_from_id: int | None = Field(
        default=None, foreign_key="workflow_record.id")

    # 错误处理
    on_error: str = Field(default="stop", max_length=50)
    max_retries: int = Field(default=0)

    author: str = Field(default="", max_length=100)
    version: str = Field(default="1.0.0", max_length=50)

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
