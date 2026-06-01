"""
Base Action - 操作基类 (简化 OOP 设计)

核心设计理念：
1. dataclass 风格：参数在初始化时设置，方便取值和赋值
2. page 属性：BaseAction 持有 page 对象
3. 单层验证：只验证当前 action 参数
4. input/output：输入输出变量管理
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Type, Optional, List, Dict, Callable
from datetime import datetime
from enum import Enum

from loguru import logger


class ExecutionPhase(Enum):
    """执行阶段"""
    VALIDATION = "validation"
    PRE_EXECUTION = "pre_execution"
    EXECUTION = "execution"
    POST_EXECUTION = "post_execution"
    CLEANUP = "cleanup"


@dataclass
class ActionContext:
    """
    操作执行上下文
    
    设计：使用 dataclass，方便参数的取值和赋值
    """
    session_id: str
    browser_id: str
    page: Any = None  # Playwright Page 对象
    browser: Any = None  # Playwright BrowserContext
    
    # 输入输出变量
    input: Dict[str, Any] = field(default_factory=dict)  # 输入变量（全局）
    output: List[str] = field(default_factory=list)  # 输出变量名列表
    variables: Dict[str, Any] = field(default_factory=dict)  # 运行时变量池
    
    # 执行状态
    params: Dict[str, Any] = field(default_factory=dict)  # 动作参数
    execution_stack: List[str] = field(default_factory=list)  # 执行栈（循环检测）
    
    def get_var(self, name: str, default: Any = None) -> Any:
        """获取变量（优先 variables，其次 input）"""
        if name in self.variables:
            return self.variables[name]
        return self.input.get(name, default)
    
    def set_var(self, name: str, value: Any):
        """设置变量"""
        self.variables[name] = value
    
    def set_output(self, name: str, value: Any):
        """设置输出变量"""
        self.variables[name] = value
        if name not in self.output:
            self.output.append(name)
    
    def get_all_vars(self) -> Dict[str, Any]:
        """获取所有变量"""
        result = dict(self.input)
        result.update(self.variables)
        return result


@dataclass
class ActionResult:
    """操作执行结果"""
    success: bool = False
    data: Any = None
    error: Optional[str] = None
    execution_time: float = 0.0
    action_id: str = ""
    action_name: str = ""
    logs: List[str] = field(default_factory=list)
    output: Dict[str, Any] = field(default_factory=dict)  # 输出变量


@dataclass
class BaseAction(ABC):
    """
    操作基类（dataclass 风格）
    
    设计要点：
    1. 使用 dataclass，所有属性在初始化时设置
    2. 持有 page 属性，可直接访问浏览器页面
    3. input/output 统一管理变量
    4. 子类只需实现 _do_execute() 方法
    """
    
    # 类属性：动作ID（子类必须定义）
    action_id: str = ""
    action_name: str = ""
    
    # 实例属性
    page: Any = None  # Playwright Page 对象
    params: Dict[str, Any] = field(default_factory=dict)  # 动作参数
    timeout: int = 30000  # 超时时间(ms)
    
    # 输入输出
    input: Dict[str, Any] = field(default_factory=dict)  # 输入变量
    output: List[str] = field(default_factory=list)  # 输出变量名列表
    
    # 内部状态
    _logs: List[str] = field(default_factory=list, repr=False)
    _phase: ExecutionPhase = ExecutionPhase.VALIDATION
    _variables: Dict[str, Any] = field(default_factory=dict, repr=False)
    
    def __post_init__(self):
        """初始化后设置默认值"""
        if not self.action_id:
            self.action_id = self.get_action_id()
        if not self.action_name:
            self.action_name = self.get_action_name()
    
    @staticmethod
    @abstractmethod
    def get_action_id() -> str:
        """返回动作ID（子类必须实现）"""
        ...
    
    @classmethod
    def get_action_name(cls) -> str:
        """返回动作名称"""
        return cls.__name__.replace("Action", "")
    
    @classmethod
    def get_params_schema(cls) -> Optional[Dict[str, Any]]:
        """获取参数 Schema（子类可覆盖）"""
        return None
    
    def get_var(self, name: str, default: Any = None) -> Any:
        """获取变量"""
        return self._variables.get(name, default)
    
    def set_var(self, name: str, value: Any):
        """设置变量"""
        self._variables[name] = value
        if name not in self.output:
            self.output.append(name)
    
    def add_log(self, message: str):
        """添加日志"""
        self._logs.append(f"[{self._phase.value}] {message}")
    
    def get_logs(self) -> List[str]:
        """获取日志"""
        return self._logs.copy()
    
    def clear_logs(self):
        """清空日志"""
        self._logs.clear()
    
    def validate_params(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证参数（单层验证，子类可覆盖）"""
        return True, None
    
    def preview(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """预览（单层）"""
        return self.validate_params(params)
    
    async def execute(self, ctx: ActionContext) -> ActionResult:
        """
        执行动作
        
        执行流程：
        1. 同步上下文参数到 action
        2. 参数验证
        3. 前置处理
        4. 执行 _do_execute()
        5. 后置处理
        6. 同步 output 到上下文
        """
        import time
        start_time = time.time()
        
        self.clear_logs()
        self._phase = ExecutionPhase.VALIDATION
        
        # 同步上下文参数
        self.page = ctx.page
        self.params = ctx.params
        self.input = dict(ctx.input)
        self._variables = ctx.get_all_vars()
        
        # 合并 input 到 variables
        for k, v in self.input.items():
            if k not in self._variables:
                self._variables[k] = v
        
        ctx.merge_input_to_variables() if hasattr(ctx, 'merge_input_to_variables') else None
        
        # 验证参数
        valid, error_msg = self.validate_params(ctx.params)
        if not valid:
            self.add_log(f"参数验证失败: {error_msg}")
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.action_id,
                action_name=self.action_name,
                logs=self.get_logs(),
            )
        
        self.add_log("参数验证通过")
        
        # 前置处理
        self._phase = ExecutionPhase.PRE_EXECUTION
        await self._pre_execute(ctx)
        
        # 执行
        self._phase = ExecutionPhase.EXECUTION
        result = await self._do_execute(ctx)
        result.execution_time = time.time() - start_time
        result.action_id = self.action_id
        result.action_name = self.action_name
        result.logs = self.get_logs()
        
        # 设置 output
        result.output = {name: self._variables.get(name) for name in self.output if name in self._variables}
        
        # 后置处理
        self._phase = ExecutionPhase.POST_EXECUTION
        await self._post_execute(ctx, result)
        
        # 同步 output 到上下文
        for name in self.output:
            if name in self._variables:
                ctx.set_output(name, self._variables[name])
        
        self._phase = ExecutionPhase.CLEANUP
        return result
    
    async def _pre_execute(self, ctx: ActionContext):
        """前置处理（子类可覆盖）"""
        self.add_log(f"开始执行: {self.action_id}")
    
    async def _post_execute(self, ctx: ActionContext, result: ActionResult):
        """后置处理（子类可覆盖）"""
        if result.success:
            self.add_log(f"执行成功，耗时: {result.execution_time:.2f}s")
        else:
            self.add_log(f"执行失败: {result.error}")
    
    @abstractmethod
    async def _do_execute(self, ctx: ActionContext) -> ActionResult:
        """实际执行逻辑（子类必须实现）"""
        ...


@dataclass
class CompositeAction(BaseAction, ABC):
    """
    组合动作基类
    
    支持步骤列表执行
    """
    steps: List[Dict[str, Any]] = field(default_factory=list)
    _registry: Any = None
    
    def set_registry(self, registry):
        """设置注册表"""
        self._registry = registry
    
    async def _do_execute(self, ctx: ActionContext) -> ActionResult:
        """组合动作执行"""
        from app.services.execution.action_executor import action_executor
        
        results = await action_executor.execute_steps(
            steps=self.steps,
            ctx=ctx,
            registry=self._registry,
        )
        
        total = len(results)
        success_count = sum(1 for r in results if r.success)
        
        last_result = results[-1] if results else None
        return ActionResult(
            success=last_result.success if last_result else False,
            data={
                "total_steps": total,
                "success_count": success_count,
                "results": [{"action_id": r.action_id, "success": r.success} for r in results]
            },
            error=last_result.error if last_result and not last_result.success else None,
            execution_time=sum(r.execution_time for r in results),
            action_id=self.action_id,
            action_name=self.action_name,
            logs=self.get_logs(),
        )


@dataclass  
class PluginAction(BaseAction, ABC):
    """
    插件动作基类
    
    作为钩子挂载到其他动作的生命周期
    """
    hook_type: str = "after_action"  # before_action, after_action, on_success, on_error
    steps: List[Dict[str, Any]] = field(default_factory=list)
    _registry: Any = None
    
    def set_registry(self, registry):
        self._registry = registry
    
    async def _do_execute(self, ctx: ActionContext) -> ActionResult:
        """插件执行"""
        from app.services.execution.action_executor import action_executor
        
        self.add_log(f"执行插件 '{self.action_name}' (hook: {self.hook_type})")
        
        results = await action_executor.execute_steps(
            steps=self.steps,
            ctx=ctx,
            registry=self._registry,
        )
        
        last_result = results[-1] if results else None
        return ActionResult(
            success=last_result.success if last_result else False,
            data={"hook_type": self.hook_type, "results": results},
            error=last_result.error if last_result and not last_result.success else None,
            execution_time=sum(r.execution_time for r in results),
            action_id=self.action_id,
            action_name=self.action_name,
            logs=self.get_logs(),
        )
