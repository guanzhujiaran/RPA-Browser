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
    page: Any = None
    browser: Any = None
    
    variables: Dict[str, Any] = field(default_factory=dict)
    output: List[str] = field(default_factory=list)
    execution_stack: List[str] = field(default_factory=list)
    
    def get_var(self, name: str, default: Any = None) -> Any:
        return self.variables.get(name, default)
    
    def set_var(self, name: str, value: Any):
        self.variables[name] = value
    
    def set_output(self, name: str, value: Any):
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
    output: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BaseAction(ABC):
    """
    操作基类（dataclass 风格）
    
    设计要点：
    1. 所有属性在初始化时赋值，execute() 无需传参
    2. 持有 page 对象，子类直接用 self.page
    3. 持有 params，子类直接用 self.params["selector"] 等
    4. ctx 仅用于共享变量池
    """
    
    action_id: str = ""
    action_name: str = ""
    
    page: Any = None
    browser: Any = None
    
    params: Dict[str, Any] = field(default_factory=dict)
    timeout: int = 30000
    
    input: Dict[str, Any] = field(default_factory=dict)
    output: List[str] = field(default_factory=list)
    
    variables: Dict[str, Any] = field(default_factory=dict)
    
    _logs: List[str] = field(default_factory=list, repr=False)
    _phase: ExecutionPhase = field(default=ExecutionPhase.VALIDATION, repr=False)
    
    params_model: Any = None
    
    def __post_init__(self):
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
    
    @property
    def metadata(self):
        """返回动作元数据"""
        return self.get_metadata()
    
    def get_metadata(self) -> Any:
        """获取动作元数据（子类可覆盖）"""
        return None
    
    def add_log(self, message: str):
        """添加日志"""
        self._logs.append(f"[{self._phase.value}] {message}")
    
    def get_logs(self) -> List[str]:
        return self._logs.copy()
    
    def clear_logs(self):
        self._logs.clear()
    
    def validate_params_with_model(self, params: Dict[str, Any]) -> tuple[bool, str, Any]:
        """使用模型验证参数"""
        if not self.params_model:
            class MockParams:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        if k == 'position' and isinstance(v, dict):
                            class Position:
                                def __init__(self, x, y):
                                    self.x = x
                                    self.y = y
                            setattr(self, k, Position(v.get('x', 0), v.get('y', 0)))
                        else:
                            setattr(self, k, v)
                
                def __getattr__(self, name):
                    if name == 'click_count':
                        return 1
                    elif name == 'delay':
                        return 0
                    elif name == 'timeout':
                        return 30000
                    elif name == 'force':
                        return False
                    elif name == 'trial':
                        return False
                    elif name == 'button':
                        return 'left'
                    elif name == 'state':
                        return 'visible'
                    elif name == 'quality':
                        return 80
                    elif name == 'full_page':
                        return False
                    elif name == 'omit_background':
                        return False
                    elif name == 'wait_until':
                        return 'load'
                    elif name == 'count':
                        return 1
                    elif name == 'condition':
                        return ''
                    elif name == 'type':
                        return 'png'
                    return None
            return True, "", MockParams(**params)
        
        try:
            validated = self.params_model(**params)
            return True, "", validated
        except Exception as e:
            return False, str(e), None
    
    def get_parameters_from_model(self) -> List[Dict[str, Any]]:
        """从模型获取参数列表"""
        return []
    
    def get_full_schema(self) -> Dict[str, Any]:
        """获取完整的 JSON Schema"""
        return {}
    
    @abstractmethod
    async def execute(self, ctx: ActionContext = None) -> ActionResult:
        """
        执行动作（子类必须实现）
        
        ctx 仅用于共享变量池，不传递 page/params。
        """
        ...


@dataclass
class CompositeAction(BaseAction, ABC):
    """组合动作基类"""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    _registry: Any = field(default=None, repr=False)
    
    def set_registry(self, registry):
        self._registry = registry
    
    @staticmethod
    def get_action_id() -> str:
        return "composite"
    
    async def execute(self, ctx: ActionContext = None) -> ActionResult:
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
    """插件动作基类"""
    hook_type: str = "after_action"
    steps: List[Dict[str, Any]] = field(default_factory=list)
    _registry: Any = field(default=None, repr=False)
    
    def set_registry(self, registry):
        self._registry = registry
    
    @staticmethod
    def get_action_id() -> str:
        return "plugin"
    
    async def execute(self, ctx: ActionContext = None) -> ActionResult:
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