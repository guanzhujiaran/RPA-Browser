"""
Core 模块 - 统一数据模型 (Unified Action Design)

核心设计理念（OOP）：
1. 统一数据模型：Action 和 Plugin 合并为 UnifiedAction
2. 单一入口：Workflow 只引用一个 action_id
3. 执行追踪：每个 action/plugin 执行都有记录
4. 动态规划执行：使用 DP 算法展开操作链

设计原则：
- action 和 plugin 在执行时自动判断类型
- 子 action 不进行预览验证，只验证一层
- 执行时记录每个操作和插件的执行记录
"""

from typing import Any, Dict, List, Optional, Generic
from datetime import datetime
import uuid
from sqlalchemy import Column, JSON, Index
from sqlmodel import SQLModel, Field
from enum import StrEnum, IntEnum


class ActionCategory(StrEnum):
    """动作类别 - 区分原子动作和组合动作"""
    ATOMIC = "atomic"           # 原子动作（系统预定义）
    COMPOSITE = "composite"      # 组合动作（用户定义的动作序列）
    PLUGIN = "plugin"            # 插件动作（钩子执行）


class HookType(StrEnum):
    """钩子类型 - 定义插件在何时执行"""
    BEFORE_ACTION = "before_action"    # 操作前执行
    AFTER_ACTION = "after_action"      # 操作后执行
    ON_SUCCESS = "on_success"          # 成功时执行
    ON_ERROR = "on_error"              # 错误时执行
    ON_TIMEOUT = "on_timeout"          # 超时时执行


class ExecutionStatus(StrEnum):
    """执行状态"""
    PENDING = "pending"        # 待执行
    RUNNING = "running"        # 执行中
    SUCCESS = "success"        # 成功
    FAILED = "failed"          # 失败
    TIMEOUT = "timeout"        # 超时
    CANCELLED = "cancelled"    # 取消


class TriggerType(StrEnum):
    """触发类型"""
    MANUAL = "manual"          # 手动触发
    SCHEDULED = "scheduled"    # 定时触发
    EVENT = "event"            # 事件触发


class ResourceType(IntEnum):
    """资源类型枚举"""
    UNIFIED_ACTION = 1         # 统一动作


class ReportReason(IntEnum):
    """举报理由枚举"""
    SPAM = 1
    INAPPROPRIATE = 2
    VIOLATION = 3
    PLAGIARISM = 4
    OTHER = 5


class ActionParameter(SQLModel):
    """操作参数定义"""
    name: str = Field(description="参数名称")
    json_schema: dict[str, Any] = Field(description="完整的 JSON Schema")


class ActionMetadata(SQLModel):
    """操作元数据（内部使用）"""
    id: str = Field(description="操作ID")
    name: str = Field(description="操作名称")
    category: ActionCategory = Field(description="动作类别")
    description: str = Field(default="", description="操作描述")
    parameters: List[ActionParameter] = Field(default_factory=list)
    json_schema: dict[str, Any] | None = Field(default=None)
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    requires_browser: bool = Field(default=True)


class ActionResult(SQLModel, Generic[Any]):
    """操作执行结果"""
    success: bool = Field(description="是否成功")
    data: Optional[Any] = Field(default=None, description="返回数据")
    error: Optional[str] = Field(default=None, description="错误信息")
    execution_time: float = Field(default=0.0, description="执行时间(秒)")
    action_id: str = Field(default="", description="操作ID")
    action_name: str = Field(default="", description="操作名称")
    logs: List[str] = Field(default_factory=list)


class ActionContext(SQLModel):
    """操作执行上下文"""
    session_id: str
    browser_id: str
    page: Any = Field(default=None, description="Playwright Page 对象")
    browser: Any = Field(default=None, description="Playwright BrowserContext 对象")
    params: Dict[str, Any] = Field(default_factory=dict)
    user_data: Dict[str, Any] = Field(default_factory=dict)
    execution_stack: List[str] = Field(default_factory=list, description="执行栈（用于循环检测）")
    variables: Dict[str, Any] = Field(default_factory=dict, description="运行时变量")


class ExecutionRecord(SQLModel, table=True):
    """
    统一动作表 - 合并 CustomAction 和 UserPlugin

    核心字段设计：
    - action_id: 唯一标识（ca_xxx 或 plugin_xxx）
    - category: 类别（atomic/composite/plugin）
    - hook_type: 钩子类型（仅 plugin 类别使用）
    - entry_action_id: 入口动作（仅 composite 使用，引用原子动作ID）

    执行判断逻辑：
    1. category == "atomic": 直接执行预定义动作
    2. category == "composite": 执行 entry_action_id 引用的动作序列
    3. category == "plugin": 作为钩子执行，可附加到其他动作的生命周期
    """
    __tablename__ = "unified_action"
    __table_args__ = (
        Index('idx_unified_user_name_unique', 'mid', 'name', unique=True),
        Index('idx_unified_action_id_unique', 'action_id', unique=True),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    action_id: str = Field(
        index=True,
        unique=True,
        max_length=100,
        description="唯一标识（系统生成）"
    )
    name: str = Field(max_length=200, description="显示名称")
    category: ActionCategory = Field(
        default=ActionCategory.COMPOSITE,
        description="动作类别: atomic/composite/plugin"
    )
    description: str = Field(default="", max_length=500)

    # 组合动作字段
    entry_action_id: Optional[str] = Field(
        default=None,
        max_length=100,
        description="入口动作ID（组合动作专用，引用原子动作或另一个组合动作）"
    )
    parameters_schema: List[Dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="参数定义JSON"
    )
    steps: List[Dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="步骤列表JSON（仅组合动作使用）"
    )

    # 插件字段
    hook_type: Optional[str] = Field(
        default=None,
        max_length=50,
        description="钩子类型（仅插件类别使用）"
    )
    target_action_id: Optional[str] = Field(
        default=None,
        max_length=100,
        description="目标动作ID（插件专用，指定挂载到哪个动作）"
    )

    # 元数据
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    user_data: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))

    # 所有权
    mid: int = Field(index=True, description="用户ID")

    # 状态
    is_enabled: bool = Field(default=True)
    is_public: bool = Field(default=False)
    timeout: int = Field(default=30000)

    # 社区功能
    likes_count: int = Field(default=0)
    reports_count: int = Field(default=0)
    is_verified: bool = Field(default=False)
    forks_count: int = Field(default=0)
    forked_from_id: Optional[int] = Field(default=None, foreign_key="unified_action.id")

    author: str = Field(default="", max_length=100)
    version: str = Field(default="1.0.0", max_length=50)

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def is_atomic(self) -> bool:
        """判断是否为原子动作"""
        return self.category == ActionCategory.ATOMIC

    def is_composite(self) -> bool:
        """判断是否为组合动作"""
        return self.category == ActionCategory.COMPOSITE

    def is_plugin(self) -> bool:
        """判断是否为插件"""
        return self.category == ActionCategory.PLUGIN

    def get_steps(self) -> List[Dict[str, Any]]:
        """获取步骤列表"""
        return self.steps or []


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
    )

    id: Optional[int] = Field(default=None, primary_key=True)
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
    crontab_expression: Optional[str] = Field(
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
    user_data: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))

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
    forked_from_id: Optional[int] = Field(default=None, foreign_key="workflow_record.id")

    # 错误处理
    on_error: str = Field(default="stop", max_length=50)
    max_retries: int = Field(default=0)

    author: str = Field(default="", max_length=100)
    version: str = Field(default="1.0.0", max_length=50)

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ActionExecutionLog(SQLModel, table=True):
    """
    动作执行日志表

    记录每一次动作或插件的执行详情。
    使用树形结构追踪执行链路。
    """
    __tablename__ = "action_execution_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    execution_id: str = Field(index=True, max_length=100, description="执行批次ID")
    parent_execution_id: Optional[str] = Field(
        default=None,
        max_length=100,
        description="父执行ID（用于树形结构）"
    )

    action_id: str = Field(index=True, max_length=100, description="动作ID")
    action_name: str = Field(default="", max_length=200)
    category: ActionCategory = Field(description="动作类别")

    status: ExecutionStatus = Field(description="执行状态")

    params: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="实际执行参数"
    )
    result_data: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON),
        description="执行结果数据"
    )
    error_message: Optional[str] = Field(default=None, max_length=1000)

    execution_time: float = Field(default=0.0, description="执行时长(秒)")

    depth: int = Field(default=0, description="执行深度（用于追踪嵌套）")
    order: int = Field(default=0, description="同层级执行顺序")

    logs: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="执行日志"
    )

    mid: int = Field(index=True)
    workflow_id: Optional[str] = Field(
        default=None,
        index=True,
        max_length=100,
        description="关联的工作流ID"
    )

    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: Optional[datetime] = Field(default=None)


class WorkflowExecutionSession(SQLModel, table=True):
    """
    工作流执行会话表

    记录工作流级别的执行会话。
    """
    __tablename__ = "workflow_execution_session"

    id: Optional[int] = Field(default=None, primary_key=True)
    execution_id: str = Field(index=True, unique=True, max_length=100)
    workflow_id: str = Field(index=True, max_length=100)

    mid: int = Field(index=True)
    browser_id: Optional[str] = Field(default=None, max_length=100)
    session_id: Optional[str] = Field(default=None, max_length=100)

    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)

    total_steps: int = Field(default=0)
    completed_steps: int = Field(default=0)
    failed_steps: int = Field(default=0)

    total_time: float = Field(default=0.0)
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: Optional[datetime] = Field(default=None)

    trigger_type: TriggerType = Field(default=TriggerType.MANUAL)
    trigger_params: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )

    user_data: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))


class ResourceLike(SQLModel, table=True):
    """资源点赞表"""
    __tablename__ = "resource_like"
    __table_args__ = (
        Index('idx_unique_like', 'mid', 'resource_type', 'resource_id', unique=True),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    mid: int = Field(index=True)
    resource_type: int = Field(index=True)
    resource_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.now)


class ResourceReport(SQLModel, table=True):
    """资源举报表"""
    __tablename__ = "resource_report"
    __table_args__ = (
        Index('idx_unique_report', 'mid', 'resource_type', 'resource_id'),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    mid: int = Field(index=True)
    resource_type: int = Field(index=True)
    resource_id: int = Field(index=True)
    reason: int = Field(description="举报理由")
    description: str = Field(default="", max_length=500)
    is_valid: bool = Field(default=True)
    reviewed_by_mid: Optional[int] = Field(default=None)
    reviewed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)


__all__ = [
    # 枚举
    "ActionCategory",
    "HookType",
    "ExecutionStatus",
    "TriggerType",
    "ResourceType",
    "ReportReason",
    # 执行相关
    "ActionParameter",
    "ActionMetadata",
    "ActionResult",
    "ActionContext",
    # 数据库模型
    "ExecutionRecord",
    "WorkflowRecord",
    "ActionExecutionLog",
    "WorkflowExecutionSession",
    "ResourceLike",
    "ResourceReport",
]
