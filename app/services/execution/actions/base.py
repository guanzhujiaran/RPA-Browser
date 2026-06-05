"""
Base Action - 操作基类 (简化 OOP 设计)

核心设计理念：
1. dataclass 风格：参数在初始化时设置，方便取值和赋值
2. page 属性：BaseAction 持有 page 对象
3. execute() 无需传参：所有属性在初始化时已赋值
4. input_vars/output_vars：输入输出变量管理
"""
import types
from app.models.execution.action_params import BuiltinActionName, BuiltinActionType, AllActionParams
import contextlib
from typing import Type
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
    _action_name: BuiltinActionName | str | None = None
    mid: int = 0  # 有的action可能会用上
    page: Page = None
    params: AllActionParams | None = None
    timeout: int = 30000
    input_vars: Dict[str, Any] = field(default_factory=dict)
    output_vars: List[str] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)
    _logs: List[str] = field(default_factory=list, repr=False)
    _phase: ExecutionPhase = field(
        default=ExecutionPhase.VALIDATION, repr=False)
    
    @classmethod
    @abstractmethod
    def new_action(
        cls,
        *,
        mid: int,
        page: Page,
        variables: Dict[str, Any],
        params: AllActionParams | None = None,
        timeout: int = 30000,
        input_vars: Dict[str, Any] = None,
        output_vars: List[str] = None,
        action_name: BuiltinActionName | str | None = None,
    ) -> 'BaseAction':
        safe_params = params or {}
        safe_input = input_vars or {}
        safe_output = output_vars or []
        safe_variables = variables or {}

        # 若 safe_params 是 dict，尝试转换为 cls 声明的 params 模型
        if isinstance(safe_params, dict):
            params_annotation = cls.__annotations__.get('params')
            if params_annotation is not None:
                # 处理 Optional[X | None] → 提取实际类型
                actual_type = params_annotation
                if isinstance(actual_type, types.UnionType):
                    non_none = [a for a in actual_type.__args__ if a is not type(None)]
                    if non_none:
                        actual_type = non_none[0]
                if isinstance(actual_type, type) and actual_type is not dict:
                    try:
                        safe_params = actual_type(**safe_params)
                    except Exception:
                        pass  # 转换失败则保持 dict

        # 从类的 class 属性获取 action_id
        kwargs = {
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': safe_input,
            'output_vars': safe_output,
            'variables': safe_variables,
        }
        if hasattr(cls, 'action_id'):
            kwargs['action_id'] = cls.action_id
        # 允许自定义 _action_name，否则由 action_name property 自动从 action_type 推导
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    @property
    def params_model(self) -> Type[AllActionParams]:
        return self.action_type.params_model

    @property
    def action_type(self) -> BuiltinActionType:
        """返回动作类型"""
        return BuiltinActionType(self.action_id) or BuiltinActionType.COMPOSITE

    @property
    def action_name(self) -> BuiltinActionName:
        """返回动作名称，优先使用自定义的 _action_name，否则从 action_type.name 推导"""
        if self._action_name is not None:
            with contextlib.suppress(ValueError):
                return BuiltinActionName(self._action_name)
        # BuiltinActionType 和 BuiltinActionName 成员名相同，通过 .name 映射
        return BuiltinActionName[self.action_type.name]

    @action_name.setter
    def action_name(self, value: BuiltinActionName | str):
        self._action_name = value

    @property
    def browser(self):
        return self.page.browser

    def __post_init__(self):
        if not self.mid:
            raise ValueError("mid is required")
        if self.variables is None:
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

    def validate_params_with_model(self, params: AllActionParams | None = None) -> tuple[bool, str, Any]:
        """使用模型验证参数"""
        target = params if params is not None else self.params
        if not self.params_model:
            raise ValueError(f"Action with no params_model: {self}")
        try:
            validated = self.params_model.model_validate(target)
            return True, "", validated
        except Exception as e:
            return False, str(e), None

    def validate_params(self, params: dict[str, Any] | None = None) -> tuple[bool, str | None]:
        """验证参数（execution_engine 调用接口）"""
        if params is None:
            return True, None
        if not self.params_model:
            return True, None
        try:
            self.params_model.model_validate(params)
            return True, None
        except Exception as e:
            return False, str(e)

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
