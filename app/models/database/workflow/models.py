"""
Core 模块 - 工作流数据库模型

定义工作流、自定义操作、用户插件等数据库模型。
"""
from app.models.execution.action_params import WorkflowStep
from app.models.execution.action_params import BuiltinActionType
from typing import Any, Dict, List
from datetime import datetime
from pydantic import field_validator
from sqlalchemy import Column, JSON, Index
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
    """资源类型枚举（社区举报用）"""
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


class ReportDecision(StrEnum):
    """举报处理决策"""
    PENDING = "pending"
    IGNORED = "ignored"      # 标记无效/忽略，资源保持不变
    WARNED = "warned"        # 警告被举报人（通知待私信系统建成后接入）
    TAKEDOWN = "takedown"    # 下架资源（设为非公开，从社区隐藏）


class ExecutionStatus(StrEnum):
    """执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# region ============ 社区资源基类 ============


class CommunityResourceBase(SQLModel):
    """社区资源基类 - mid 字段：数据库存储为 str，运行时使用 int"""
    mid: str = Field(max_length=255, index=True, description="当前所有者用户ID")
    original_mid: str = Field(
        max_length=255, index=True, description="最初创建者用户ID")
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


# region ============ 数据库模型 ============


class CompositeActionModel(CommunityResourceBase, table=True):
    """
    复合动作表

    用户定义的、可复用的动作组合（类似函数）。
    """
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
    parameters_schema: List[Dict] = Field(
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
    input_vars: List[Dict] = Field(
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
        foreign_key="compositeactionmodel.id",
        description="Fork 来源的操作ID"
    )
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False)
    retry_times: int = Field(default=0)
    retry_delay: float = Field(default=1.0)

    # === 操作日志采集配置（随 action 一起增删改，无需单独设置） ===
    log_enabled: bool = Field(
        default=False, description="是否采集该操作的执行日志")
    log_record_params: bool = Field(default=True, description="是否记录变量替换后的入参")
    log_record_result: bool = Field(default=True, description="是否记录执行返回结果")
    log_record_variables: bool = Field(default=False, description="是否记录变量池快照")
    log_only_on_error: bool = Field(default=False, description="仅在执行失败时记录")
    log_max_payload_length: int = Field(
        default=4000, description="params/result/variables 序列化后最大字符数，超出则截断")
    log_retention_days: int = Field(
        default=30, description="日志保留天数，0 表示永久保留")


class TagModel(SQLModel, table=True):
    """标签表（多对多）"""
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, max_length=100, index=True)


class CompositeActionTagLink(SQLModel, table=True):
    """复合操作-标签多对多关联表"""
    composite_action_id: int = Field(foreign_key="compositeactionmodel.id", primary_key=True)
    tag_id: int = Field(foreign_key="tagmodel.id", primary_key=True)


class WorkflowPluginRelation(SQLModel, table=True):
    """工作流插件关联表"""

    id: int | None = Field(default=None, primary_key=True,)
    workflow_id: str = Field(
        foreign_key="userworkflow.workflow_id", index=True, description="工作流ID")
    plugin_id: str = Field(
        foreign_key="userplugin.plugin_id", index=True, description="插件ID")
    config_params: Dict = Field(
        default_factory=dict, sa_column=Column(JSON), description="配置参数")


class UserPlugin(CommunityResourceBase, table=True):
    """用户插件表"""
    __table_args__ = (
        Index('idx_user_plugin_name_unique', 'mid', 'name', unique=True),
    )

    id: int = Field(default=None, primary_key=True,)
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
        foreign_key="userplugin.id",
        description="Fork 来源的插件ID"
    )
    priority: int = Field(default=100, description="优先级")


class UserWorkflow(CommunityResourceBase, table=True):
    """用户工作流表 - 定时任务调度配置"""
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
        foreign_key="userworkflow.id",
        description="Fork 来源的工作流ID"
    )
    trigger_type: str = Field(
        default="manual", max_length=50, description="触发类型")
    trigger_config: Dict = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="触发配置"
    )


class ResourceReport(SQLModel, table=True):
    """资源举报表（社区举报归属各业务系统，此处为 RPA 资源举报）"""
    __table_args__ = (
        Index('idx_unique_report', 'mid', 'resource_type', 'resource_id'),
        {"extend_existing": True},
    )

    id: int | None = Field(primary_key=True,)
    mid: str = Field(max_length=255, index=True, description="举报用户ID")
    resource_type: ResourceType = Field(index=True, description="资源类型")
    resource_id: int = Field(index=True, description="资源ID")
    reason: ReportReason = Field(description="举报理由")
    description: str = Field(default="", max_length=500, description="详细描述")
    is_valid: bool = Field(default=True, description="是否有效")
    reviewed_by_mid: str | None = Field(
        default=None, max_length=255, description="审核管理员ID")
    reviewed_at: datetime | None = Field(default=None, description="审核时间")
    decision: ReportDecision = Field(
        default=ReportDecision.PENDING, description="处理决策：pending/ignored/warned/takedown")
    review_note: str = Field(default="", max_length=500, description="审核备注")
    created_at: datetime = Field(default_factory=datetime.now)


class WorkflowRecord(SQLModel, table=True):
    """
    工作流记录表 - 简化设计

    核心变化：
    - 只允许一个 action_id 入口
    - 不再支持嵌套的 steps 结构
    - 通过 crontab 表达式支持周期运行
    """
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
    params_template: Dict = Field(
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
    input: Dict = Field(
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
    mid: str = Field(max_length=255, index=True)

    # 状态
    is_enabled: bool = Field(default=True)
    is_public: bool = Field(default=False)

    # 社区功能
    likes_count: int = Field(default=0)
    reports_count: int = Field(default=0)
    is_verified: bool = Field(default=False)
    forks_count: int = Field(default=0)
    forked_from_id: int | None = Field(
        default=None, foreign_key="workflowrecord.id")

    # 错误处理
    on_error: ErrorHandlingEnum = Field(default=ErrorHandlingEnum.STOP)
    max_retries: int = Field(default=0)

    author: str = Field(..., max_length=100)
    version: str = Field(default="1.0.0", max_length=50)

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
