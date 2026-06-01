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

from typing import Any, Dict, List, Optional, Generic, ForwardRef, Union
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


class ActionResult(SQLModel):
    """
    操作执行结果
    
    核心设计：
    - output: 输出变量字典，运行结束后赋值到上下文
    """
    success: bool = Field(description="是否成功")
    data: Optional[Any] = Field(default=None, description="返回数据")
    error: Optional[str] = Field(default=None, description="错误信息")
    execution_time: float = Field(default=0.0, description="执行时间(秒)")
    action_id: str = Field(default="", description="操作ID")
    action_name: str = Field(default="", description="操作名称")
    logs: List[str] = Field(default_factory=list)
    
    # 输出变量
    output: Dict[str, Any] = Field(default_factory=dict, description="输出变量，运行结束后赋值到上下文")


class ActionContext(SQLModel):
    """
    操作执行上下文
    
    核心设计：
    - input: 输入变量（dict），运行时全局变量
    - output: 输出变量名列表（List[str]），运行结束后赋值
    - variables: 运行时变量池，合并所有 input 和 output
    """
    session_id: str
    browser_id: str
    page: Any = Field(default=None, description="Playwright Page 对象")
    browser: Any = Field(default=None, description="Playwright BrowserContext 对象")
    params: Dict[str, Any] = Field(default_factory=dict, description="动作参数")
    
    # 输入输出设计
    input: Dict[str, Any] = Field(default_factory=dict, description="输入变量（全局变量）")
    output: List[str] = Field(default_factory=list, description="输出变量名列表")
    
    # 运行时变量池（合并 input 和动态产生的 output）
    variables: Dict[str, Any] = Field(default_factory=dict, description="运行时变量池")
    execution_stack: List[str] = Field(default_factory=list, description="执行栈（用于循环检测）")
    
    def get_variable(self, name: str, default: Any = None) -> Any:
        """获取变量（优先从 variables，其次从 input）"""
        if name in self.variables:
            return self.variables[name]
        return self.input.get(name, default)
    
    def set_variable(self, name: str, value: Any):
        """设置变量到 variables"""
        self.variables[name] = value
    
    def set_output(self, name: str, value: Any):
        """设置输出变量（自动添加到 output 列表）"""
        self.variables[name] = value
        if name not in self.output:
            self.output.append(name)
    
    def get_all_variables(self) -> Dict[str, Any]:
        """获取所有变量（input + variables）"""
        result = dict(self.input)
        result.update(self.variables)
        return result
    
    def merge_input_to_variables(self):
        """将 input 合并到 variables（初始化时调用）"""
        for key, value in self.input.items():
            if key not in self.variables:
                self.variables[key] = value


class StepType(StrEnum):
    """步骤类型"""
    ATOMIC = "atomic"                # 原子动作
    COMPOSITE_REF = "composite_ref"  # 组合动作引用（前向引用）
    PLUGIN_REF = "plugin_ref"        # 插件引用（前向引用）
    LOOP = "loop"                    # 循环步骤
    CONDITIONAL = "conditional"      # 条件步骤


class LoopConfig(SQLModel):
    """循环配置"""
    type: str = Field(default="count", description="循环类型: count/while/until")
    value: Union[int, str] = Field(description="循环值: 次数或条件表达式")
    max_iterations: int = Field(default=100, description="最大迭代次数，防止死循环")


class ConditionalConfig(SQLModel):
    """条件分支配置"""
    condition: str = Field(description="条件表达式")
    true_branch: Optional[str] = Field(default=None, description="条件为真时的步骤列表ID")
    false_branch: Optional[str] = Field(default=None, description="条件为假时的步骤列表ID")


class StepMetadata(SQLModel):
    """步骤元数据"""
    name: str = Field(default="", description="步骤名称（用于显示）")
    description: str = Field(default="", description="步骤描述")
    retry_count: int = Field(default=0, description="重试次数")
    continue_on_error: bool = Field(default=False, description="错误时继续")


class Step(SQLModel):
    """
    步骤模型（前向引用支持）

    支持的类型：
    - ATOMIC: 原子动作，直接执行
    - COMPOSITE_REF: 组合动作引用，通过 action_id 引用
    - PLUGIN_REF: 插件引用，通过 action_id 引用
    - LOOP: 循环步骤，包含循环配置
    - CONDITIONAL: 条件步骤，包含分支配置
    """
    id: str = Field(description="步骤唯一ID（用于前向引用）")
    type: StepType = Field(description="步骤类型")

    # 核心执行字段
    action_id: Optional[str] = Field(default=None, description="动作ID（用于ATOMIC/COMPOSITE_REF/PLUGIN_REF类型）")
    params: Dict[str, Any] = Field(default_factory=dict, description="参数字典，支持模板变量")

    # 特殊配置
    loop_config: Optional[LoopConfig] = Field(default=None, description="循环配置（仅LOOP类型）")
    conditional_config: Optional[ConditionalConfig] = Field(default=None, description="条件配置（仅CONDITIONAL类型）")

    # 子步骤（嵌套支持，通过前向引用）
    children: Optional[List[str]] = Field(default=None, description="子步骤ID列表（用于LOOP/CONDITIONAL的分支）")

    # 元数据
    metadata: StepMetadata = Field(default_factory=StepMetadata, description="步骤元数据")

    class Config:
        arbitrary_types_allowed = True


# 前向引用
StepRef = ForwardRef("Step")


class StepGroup(SQLModel):
    """
    步骤组 - 支持前向引用的步骤集合

    设计说明：
    - 通过 steps 字典存储所有步骤，key 为 step_id
    - 通过 entry_id 指定入口步骤
    - 通过 children 字段实现前向引用
    """
    id: str = Field(description="步骤组ID")
    name: str = Field(default="", description="步骤组名称")
    description: str = Field(default="", description="步骤组描述")
    entry_id: Optional[str] = Field(default=None, description="入口步骤ID")
    steps: Dict[str, Step] = Field(default_factory=dict, description="步骤字典，key为step_id")

    def get_step(self, step_id: str) -> Optional[Step]:
        """获取指定步骤"""
        return self.steps.get(step_id)

    def get_entry_step(self) -> Optional[Step]:
        """获取入口步骤"""
        if not self.entry_id:
            # 如果没有指定入口，返回第一个步骤
            return next(iter(self.steps.values()), None) if self.steps else None
        return self.get_step(self.entry_id)

    def get_execution_order(self) -> List[str]:
        """获取执行顺序（简单实现：按ID排序）"""
        return list(self.steps.keys())


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
        description="步骤列表JSON（仅组合动作使用，向后兼容）"
    )
    step_group: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON),
        description="步骤组（新格式，支持前向引用）"
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
    
    # 输入输出定义
    input_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON),
        description="输入变量定义（JSON Schema 格式）"
    )
    output_schema: Optional[List[str]] = Field(
        default=None,
        sa_column=Column(JSON),
        description="输出变量名列表"
    )

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

    def has_step_group(self) -> bool:
        """判断是否有新格式的步骤组"""
        return self.step_group is not None

    def get_step_group(self) -> Optional[StepGroup]:
        """获取步骤组（解析新格式）"""
        if not self.step_group:
            return None
        try:
            return StepGroup(**self.step_group)
        except Exception:
            return None

    def get_steps(self) -> List[Dict[str, Any]]:
        """获取步骤列表（向后兼容）"""
        if self.step_group:
            # 从新格式转换为旧格式
            step_group = self.get_step_group()
            if step_group:
                return [
                    {
                        "id": step.id,
                        "type": step.type.value,
                        "action_id": step.action_id,
                        "params": step.params,
                        "metadata": step.metadata.dict() if step.metadata else None
                    }
                    for step in step_group.steps.values()
                ]
        return self.steps or []

    def set_step_group(self, step_group: StepGroup) -> None:
        """设置步骤组（新格式）"""
        self.step_group = step_group.dict()
        # 同时同步到旧格式，保持向后兼容
        self.steps = self.get_steps()

    def resolve_forward_references(self) -> Dict[str, Any]:
        """
        解析前向引用，构建执行图

        返回值:
        - 步骤图: {step_id: {children: [step_ids], ...}}
        """
        if not self.step_group:
            return {}

        step_group = self.get_step_group()
        if not step_group:
            return {}

        # 构建执行图
        graph: Dict[str, Dict[str, Any]] = {}
        for step_id, step in step_group.steps.items():
            graph[step_id] = {
                "step": step,
                "children": step.children or [],
                "resolved": []
            }

        return graph


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
    forked_from_id: Optional[int] = Field(default=None, foreign_key="workflow_record.id")

    # 错误处理
    on_error: str = Field(default="stop", max_length=50)
    max_retries: int = Field(default=0)

    author: str = Field(default="", max_length=100)
    version: str = Field(default="1.0.0", max_length=50)

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class StepBuilder:
    """
    步骤构建器 - 简化 StepGroup 的构建

    示例用法:
    ```python
    builder = StepBuilder()
    builder.add_atomic_step(step_id="s1", action_id="click", params={"selector": "#btn"})
    builder.add_composite_ref_step(step_id="s2", action_id="ca_my_custom", params={})
    step_group = builder.build()
    ```
    """

    def __init__(self):
        self.steps: Dict[str, Step] = {}
        self.entry_id: Optional[str] = None

    def add_step(self, step: Step) -> "StepBuilder":
        """添加步骤"""
        self.steps[step.id] = step
        if self.entry_id is None:
            self.entry_id = step.id
        return self

    def add_atomic_step(
        self,
        step_id: str,
        action_id: str,
        params: Optional[Dict[str, Any]] = None,
        metadata: Optional[StepMetadata] = None
    ) -> "StepBuilder":
        """添加原子动作步骤"""
        step = Step(
            id=step_id,
            type=StepType.ATOMIC,
            action_id=action_id,
            params=params or {},
            metadata=metadata or StepMetadata()
        )
        return self.add_step(step)

    def add_composite_ref_step(
        self,
        step_id: str,
        action_id: str,
        params: Optional[Dict[str, Any]] = None,
        metadata: Optional[StepMetadata] = None
    ) -> "StepBuilder":
        """添加组合动作引用步骤"""
        step = Step(
            id=step_id,
            type=StepType.COMPOSITE_REF,
            action_id=action_id,
            params=params or {},
            metadata=metadata or StepMetadata()
        )
        return self.add_step(step)

    def add_plugin_ref_step(
        self,
        step_id: str,
        action_id: str,
        params: Optional[Dict[str, Any]] = None,
        metadata: Optional[StepMetadata] = None
    ) -> "StepBuilder":
        """添加插件引用步骤"""
        step = Step(
            id=step_id,
            type=StepType.PLUGIN_REF,
            action_id=action_id,
            params=params or {},
            metadata=metadata or StepMetadata()
        )
        return self.add_step(step)

    def add_loop_step(
        self,
        step_id: str,
        loop_config: LoopConfig,
        children: Optional[List[str]] = None,
        metadata: Optional[StepMetadata] = None
    ) -> "StepBuilder":
        """添加循环步骤"""
        step = Step(
            id=step_id,
            type=StepType.LOOP,
            loop_config=loop_config,
            children=children,
            metadata=metadata or StepMetadata()
        )
        return self.add_step(step)

    def add_conditional_step(
        self,
        step_id: str,
        conditional_config: ConditionalConfig,
        metadata: Optional[StepMetadata] = None
    ) -> "StepBuilder":
        """添加条件步骤"""
        step = Step(
            id=step_id,
            type=StepType.CONDITIONAL,
            conditional_config=conditional_config,
            metadata=metadata or StepMetadata()
        )
        return self.add_step(step)

    def set_entry(self, step_id: str) -> "StepBuilder":
        """设置入口步骤"""
        if step_id in self.steps:
            self.entry_id = step_id
        return self

    def build(self, group_id: Optional[str] = None, name: str = "") -> StepGroup:
        """构建步骤组"""
        return StepGroup(
            id=group_id or f"sg_{uuid.uuid4().hex[:12]}",
            name=name,
            entry_id=self.entry_id,
            steps=self.steps
        )


def create_sample_step_group() -> StepGroup:
    """
    创建示例步骤组（演示前向引用示例）

    示例流程：
    - 步骤1: 点击按钮
    - 步骤2: 等待页面加载
    - 步骤3: 循环3次输入数据
      - 子步骤: 输入文本
      - 子步骤: 点击提交
    """
    builder = StepBuilder()

    # 添加原子步骤
    builder.add_atomic_step(
        step_id="s1_click",
        action_id="click",
        params={"selector": "#start-btn"},
        metadata=StepMetadata(name="点击开始按钮")
    )

    builder.add_atomic_step(
        step_id="s2_wait",
        action_id="wait",
        params={"timeout": 5000},
        metadata=StepMetadata(name="等待页面加载")
    )

    # 添加循环步骤（引用子步骤）
    loop_config = LoopConfig(type="count", value=3)
    builder.add_loop_step(
        step_id="s3_loop",
        loop_config=loop_config,
        children=["s4_input", "s5_submit"],
        metadata=StepMetadata(name="循环输入数据3次")
    )

    # 子步骤
    builder.add_atomic_step(
        step_id="s4_input",
        action_id="input",
        params={"selector": "#input", "value": "{{loop.index}}"},
        metadata=StepMetadata(name="输入循环值")
    )

    builder.add_atomic_step(
        step_id="s5_submit",
        action_id="click",
        params={"selector": "#submit"},
        metadata=StepMetadata(name="提交")
    )

    # 设置入口
    builder.set_entry("s1_click")

    return builder.build(name="示例工作流步骤组")


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
