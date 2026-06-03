"""
Base Action - 操作基类 (简化 OOP 设计)

核心设计理念：
1. dataclass 风格：参数在初始化时设置，方便取值和赋值
2. page 属性：BaseAction 持有 page 对象
3. execute() 无需传参：所有属性在初始化时已赋值
4. input_vars/output_vars：输入输出变量管理
"""

from typing import Type
from app.models.execution.params import AllActionParams
from app.models.database.workflow.models import BuiltinActionName
from app.models.database.workflow.models import BuiltinActionType
from app.models.database.workflow.models import ActionMetadata
from botright.playwright_mock import Page
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Dict
from enum import Enum


class ExecutionPhase(Enum):
    """执行阶段"""
    VALIDATION = "validation"
    PRE_EXECUTION = "pre_execution"
    EXECUTION = "execution"
    POST_EXECUTION = "post_execution"
    CLEANUP = "cleanup"


@dataclass
class ActionResult:
    """操作执行结果"""
    success: bool = False
    data: Any = None
    error: str | None = None
    execution_time: float = 0.0
    action_id: str = ""
    action_name: str = ""
    logs: List[str] = field(default_factory=list)
    output: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BaseAction(ABC):
    """
    操作基类（dataclass 风格）
    
    """

    action_id: BuiltinActionType | str
    _action_name: BuiltinActionName | str
    mid: int = 0  # 有的action可能会用上
    page: Page = None
    params: Dict[str, Any] = field(default_factory=dict)
    timeout: int = 30000
    input_vars: Dict[str, Any] = field(default_factory=dict)
    output_vars: List[str] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)
    _logs: List[str] = field(default_factory=list, repr=False)
    _phase: ExecutionPhase = field(
        default=ExecutionPhase.VALIDATION, repr=False)
    


    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page: Page,
        variables: Dict[str, Any],
        params: Dict[str, Any] = None,
        timeout: int = 30000,
        input_vars: Dict[str, Any] = None,
        output_vars: List[str] = None,
    ) -> "BaseAction":
        safe_params = params or {}
        safe_input = input_vars or {}
        safe_output = output_vars or []
        safe_variables = variables or {}
        return cls(
            mid=mid,
            page=page,
            params=safe_params,
            timeout=timeout,
            input_vars=safe_input,
            output_vars=safe_output,
            variables=safe_variables,
        )

    @property
    def params_model(self) -> Type[AllActionParams] | None:
        return self.action_type.params_model

    @property
    def action_type(self) -> BuiltinActionType:
        """返回动作类型"""
        return BuiltinActionType(self.action_id) or BuiltinActionType.COMPOSITE

    @property
    def action_name(self) -> BuiltinActionName:
        """返回动作名称"""
        return BuiltinActionName(self._action_name) or BuiltinActionName.COMPOSITE

    @action_name.setter
    def action_name(self, value: BuiltinActionName | str):
        self._action_name = value

    @property
    def browser(self):
        return self.page.browser

    def __post_init__(self):
        if not self.mid:
            raise ValueError("mid is required")
        if not self.page:
            raise ValueError("page is required")
        if not self.variables:
            raise ValueError("variables is required")

    @property
    def metadata(self) -> ActionMetadata:
        """返回动作元数据"""
        return self._get_metadata()

    def _get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id=self.action_id,
            name=self.action_type.nameDisplay,
            type=self.action_type,
            description=self.action_type.descDisplay,
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    def add_log(self, message: str):
        """添加日志"""
        self._logs.append(f"[{self._phase.value}] {message}")

    def get_logs(self) -> List[str]:
        return self._logs.copy()

    def clear_logs(self):
        self._logs.clear()

    def validate_params_with_model(self) -> tuple[bool, str, Any]:
        """使用模型验证参数"""
        if not self.params_model:
            raise ValueError(f"Action with no params_model: {self}")
        try:
            validated = self.params_model.model_validate(self.params)
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
    async def execute(self) -> ActionResult:
        """
        执行动作（子类必须实现）

        所有参数通过 self 访问，无需传参。
        """
        ...
