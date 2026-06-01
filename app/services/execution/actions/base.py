"""
Base Action - 操作基类 (简化 OOP 设计)

核心设计理念：
1. dataclass 风格：参数在初始化时设置，方便取值和赋值
2. page 属性：BaseAction 持有 page 对象
3. execute() 无需传参：所有属性在初始化时已赋值
4. input/output：输入输出变量管理
5. ctx 仅用于共享变量池
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Type, Optional, List, Dict
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
    
    职责：共享变量池和执行状态。
    page/params 等运行时数据在 action 初始化时赋值，不经过 ctx。
    """
    session_id: str = ""
    browser_id: str = ""
    page: Any = None  # Playwright Page 对象
    browser: Any = None  # Playwright BrowserContext
    
    # 共享变量池
    variables: Dict[str, Any] = field(default_factory=dict)
    
    # 输出收集
    output: List[str] = field(default_factory=list)
    
    # 执行状态
    execution_stack: List[str] = field(default_factory=list)
    
    def get_var(self, name: str, default: Any = None) -> Any:
        """获取变量"""
        return self.variables.get(name, default)
    
    def set_var(self, name: str, value: Any):
        """设置变量"""
        self.variables[name] = value
    
    def set_output(self, name: str, value: Any):
        """设置输出变量"""
        self.variables[name] = value
        if name not in self.output:
            self.output.append(name)


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
    1. 所有属性在初始化时赋值，execute() 无需传参
    2. 持有 page 对象，子类直接用 self.page
    3. 持有 params，子类直接用 self.params["selector"] 等
    4. ctx 仅用于共享变量池
    
    初始化方式：
        action = ClickAction(
            page=page,
            params={"selector": "#btn", "button": "left"},
            input={"url": "https://example.com"},
            output=["clicked_element"],
            variables={"loop_index": 1, "result_0": {...}},
        )
        result = await action.execute(ctx)
    """
    
    # 标识
    action_id: str = ""
    action_name: str = ""
    
    # 运行时对象（初始化时赋值）
    page: Any = None
    browser: Any = None
    
    # 动作参数（初始化时赋值）
    params: Dict[str, Any] = field(default_factory=dict)
    timeout: int = 30000
    
    # 输入输出
    input: Dict[str, Any] = field(default_factory=dict)
    output: List[str] = field(default_factory=list)
    
    # 运行时变量池（初始化时赋值，合并 input + 全局变量）
    variables: Dict[str, Any] = field(default_factory=dict)
    
    # 内部状态
    _logs: List[str] = field(default_factory=list, repr=False)
    _phase: ExecutionPhase = field(default=ExecutionPhase.VALIDATION, repr=False)
    
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
        name = cls.__name__
        if name.endswith("Action") and name != "Action":
            return name[:-6]  # 去掉末尾的 "Action"
        return name
    
    @classmethod
    def get_params_schema(cls) -> Optional[Dict[str, Any]]:
        """获取参数 Schema（子类可覆盖）"""
        return None
    
    # ---- 变量管理 ----
    
    def get_var(self, name: str, default: Any = None) -> Any:
        """获取变量"""
        return self.variables.get(name, default)
    
    def set_var(self, name: str, value: Any):
        """设置变量（自动加入 output）"""
        self.variables[name] = value
        if name not in self.output:
            self.output.append(name)
    
    # ---- 日志 ----
    
    def add_log(self, message: str):
        """添加日志"""
        self._logs.append(f"[{self._phase.value}] {message}")
    
    def get_logs(self) -> List[str]:
        return self._logs.copy()
    
    def clear_logs(self):
        self._logs.clear()
    
    # ---- 验证 ----
    
    def validate_params(self) -> tuple[bool, Optional[str]]:
        """验证参数（单层验证，子类可覆盖）"""
        return True, None
    
    def preview(self) -> tuple[bool, Optional[str]]:
        """预览（单层）"""
        return self.validate_params()
    
    # ---- 执行 ----
    
    async def execute(self, ctx: ActionContext = None) -> ActionResult:
        """
        执行动作（无需传参，所有属性已在初始化时赋值）
        
        ctx 仅用于共享变量池，不传递 page/params。
        
        执行流程：
        1. 从 ctx 合并变量到 self.variables
        2. 参数验证
        3. 前置处理
        4. 执行 _do_execute()
        5. 后置处理
        6. 将 output 同步回 ctx
        """
        import time
        start_time = time.time()
        
        self.clear_logs()
        self._phase = ExecutionPhase.VALIDATION
        
        # 从 ctx 合并变量池（不覆盖已有的）
        if ctx:
            for k, v in ctx.variables.items():
                if k not in self.variables:
                    self.variables[k] = v
            for k, v in self.input.items():
                if k not in self.variables:
                    self.variables[k] = v
        
        # 验证参数
        valid, error_msg = self.validate_params()
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
        await self._pre_execute()
        
        # 执行
        self._phase = ExecutionPhase.EXECUTION
        result = await self._do_execute()
        result.execution_time = time.time() - start_time
        result.action_id = self.action_id
        result.action_name = self.action_name
        result.logs = self.get_logs()
        
        # 收集 output
        result.output = {
            name: self.variables.get(name)
            for name in self.output
            if name in self.variables
        }
        
        # 后置处理
        self._phase = ExecutionPhase.POST_EXECUTION
        await self._post_execute(result)
        
        # 同步 output 回 ctx
        if ctx:
            for name in self.output:
                if name in self.variables:
                    ctx.set_output(name, self.variables[name])
        
        self._phase = ExecutionPhase.CLEANUP
        return result
    
    async def _pre_execute(self):
        """前置处理（子类可覆盖）"""
        self.add_log(f"开始执行: {self.action_id}")
    
    async def _post_execute(self, result: ActionResult):
        """后置处理（子类可覆盖）"""
        if result.success:
            self.add_log(f"执行成功，耗时: {result.execution_time:.2f}s")
        else:
            self.add_log(f"执行失败: {result.error}")
    
    @abstractmethod
    async def _do_execute(self) -> ActionResult:
        """
        实际执行逻辑（子类必须实现）
        
        子类直接使用 self.page、self.params 等属性。
        """
        ...


@dataclass
class CompositeAction(BaseAction, ABC):
    """
    组合动作基类
    
    支持步骤列表执行
    """
    steps: List[Dict[str, Any]] = field(default_factory=list)
    _registry: Any = field(default=None, repr=False)
    
    @staticmethod
    def get_action_id() -> str:
        return "composite"
    
    def set_registry(self, registry):
        """设置注册表"""
        self._registry = registry
    
    async def _do_execute(self) -> ActionResult:
        """组合动作执行"""
        from app.services.execution.action_executor import action_executor
        
        results = await action_executor.execute_steps(
            steps=self.steps,
            page=self.page,
            browser=self.browser,
            variables=self.variables,
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
    hook_type: str = "after_action"
    steps: List[Dict[str, Any]] = field(default_factory=list)
    _registry: Any = field(default=None, repr=False)
    
    @staticmethod
    def get_action_id() -> str:
        return "plugin"
    
    def set_registry(self, registry):
        self._registry = registry
    
    async def _do_execute(self) -> ActionResult:
        """插件执行"""
        from app.services.execution.action_executor import action_executor
        
        self.add_log(f"执行插件 '{self.action_name}' (hook: {self.hook_type})")
        
        results = await action_executor.execute_steps(
            steps=self.steps,
            page=self.page,
            browser=self.browser,
            variables=self.variables,
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
