"""
Base Action - 操作基类 (简化 OOP 设计)

核心设计理念：
1. dataclass 风格：参数在初始化时设置，方便取值和赋值
2. page 属性：BaseAction 持有 page 对象
3. execute() 无需传参：所有属性在初始化时已赋值
4. input_vars/output_vars：输入输出变量管理
"""

from bili_common.models import StrEnumAutoDoc
from sqlmodel import SQLModel
from typing import TypeVar
from typing import Generic
import types
from app.models.execution.action_params import (
    BuiltinActionName,
    BuiltinActionType,
    AllActionResult,
    build_action_metadata,
)
import contextlib
from typing import Type
from app.models.execution.action_params import ActionMetadata
from botright.playwright_mock import Page
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, List, Dict

ParamsT = TypeVar("ParamsT", bound=SQLModel)
DataT = TypeVar("DataT", default=Any)

# ============ 内置动作类注册表（依赖倒置） ============
# 各内置动作模块在底部调用 ``register_action_class`` 自注册；查找方
# （如控制流策略层按 action_id 实例化子步骤动作）只依赖本叶子模块，
# 避免「all_actions 汇总表 → control_flow → all_actions」的循环导入。

_ACTION_CLASS_REGISTRY: dict[str, type["BaseAction[Any]"]] = {}


def register_action_class(action_cls: type["BaseAction[Any]"]) -> None:
    """把内置动作类注册进注册表（key 取 action_id 的字符串 value）"""
    action_id = getattr(action_cls, "action_id", None)
    if action_id is None:
        return
    _ACTION_CLASS_REGISTRY[str(getattr(action_id, "value", action_id))] = action_cls


def get_action_class(action_id: Any) -> type["BaseAction[Any]"] | None:
    """按 action_id（内置动作名字符串 / BuiltinActionType）查找已注册的动作类"""
    if action_id is None:
        return None
    return _ACTION_CLASS_REGISTRY.get(str(getattr(action_id, "value", action_id)))


def get_registered_action_classes() -> dict[str, type["BaseAction[Any]"]]:
    """已注册内置动作类的快照（供汇总表构建，调用方拿到后即与后续注册解耦）"""
    return dict(_ACTION_CLASS_REGISTRY)


@dataclass(frozen=True)
class CustomActionResolver:
    """自定义（复合）动作解析槽位。

    由引擎层（``engine``）在模块级经 ``set_custom_action_resolver`` 填充；
    控制流策略层（``AtomicStrategy``）经 ``get_custom_action_resolver`` 查找，
    使 control_flow 无需 import action_registry → all_actions → control_flow
    这一会成环的依赖链。
    """

    get_action_class: Callable  # async (action_id) -> type[BaseAction] | None
    get_steps: Callable  # async (action_id) -> list[dict] | None


_custom_action_resolver: CustomActionResolver | None = None


def set_custom_action_resolver(resolver: CustomActionResolver) -> None:
    """填充自定义动作解析器（引擎层启动时调用）"""
    global _custom_action_resolver
    _custom_action_resolver = resolver


def get_custom_action_resolver() -> CustomActionResolver | None:
    """获取自定义动作解析器（未填充时返回 None）"""
    return _custom_action_resolver


class ExecutionPhase(StrEnumAutoDoc):
    """执行阶段"""

    VALIDATION = "validation"
    PRE_EXECUTION = "pre_execution"
    EXECUTION = "execution"
    POST_EXECUTION = "post_execution"
    CLEANUP = "cleanup"


@dataclass
class ActionResult(Generic[DataT]):
    """操作执行结果（泛型版本，DataT 为结果数据类型）"""

    success: bool = False
    data: DataT | None = None
    error: str | None = None
    execution_time: float = 0.0
    action_id: str = ""
    action_name: str = ""
    logs: List[str] = field(default_factory=list)
    output: Dict = field(default_factory=dict)
    variables: Dict = field(default_factory=dict)
    replaced_params: Dict = field(default_factory=dict)
    #: 该失败是否已被 `on_error=continue` **显式容忍**。
    #:
    #: 由 Pipeline 在按 continue 跳过失败步骤时置 True：这类失败仍进入运行消息 / 步骤日志、
    #: 仍计入 `failed_count`（便于排查），但**不参与「整次运行是否失败」的结论**——
    #: 判定失败的只有「未知异常」与「on_error=stop（含重试耗尽）」。
    tolerated: bool = False


@dataclass
class BaseAction(ABC, Generic[ParamsT]):
    """
    操作基类（dataclass 风格）
    """

    action_id: Any
    action_type: BuiltinActionType
    page: Page
    params: ParamsT
    _action_name: BuiltinActionName | str | None = None
    mid: int = 0  # 有的action可能会用上
    timeout: int = 30000
    input_vars: Dict = field(default_factory=dict)
    output_vars: List[str] = field(default_factory=list)
    variables: Dict = field(default_factory=dict)
    auth_headers: Dict[str, str] = field(default_factory=dict)
    # 执行元信息（execution_id / parent_execution_id / browser_id / session_id / workflow_id）
    # 由 ExecutionEngine 在创建实例后注入，供操作日志采集串联执行链路
    exec_meta: Dict[str, Any] = field(default_factory=dict)
    _logs: List[str] = field(default_factory=list, repr=False)
    _phase: ExecutionPhase = field(default=ExecutionPhase.VALIDATION, repr=False)

    def _merge_output_vars(self, action_result: ActionResult) -> None:
        """
        合并输出变量

        将 action_result.data 的键值对按需赋值到 variables 中：
        - 如果有 output_vars，按顺序将 data 的值赋给对应的变量名
        - 始终设置 last_output 为完整的 data

        注意：不会将 data 的所有字段展开到 variables 中，避免 params 泄漏。
        execute 完了之后必须调用
        """
        data = action_result.data
        if data is None:
            return

        # 将 data 转为 dict（支持 dict 和模型实例）
        if isinstance(data, dict):
            data_dict = data
        elif hasattr(data, "model_dump"):
            data_dict = data.model_dump()
        else:
            self.variables["last_output"] = data
            return

        # 如果有 output_vars，按顺序把 data 的值赋给 output_vars 中对应的变量名
        if self.output_vars:
            data_values = list(data_dict.values())
            for i, var_name in enumerate(self.output_vars):
                if i < len(data_values):
                    self.variables[var_name] = data_values[i]

        self.variables["last_output"] = data

    @classmethod
    def _convert_params(cls, params: Any) -> Any:
        """将 dict 类型的 params 转换为对应的 Pydantic 模型实例"""
        if not isinstance(params, dict):
            return params
        params_annotation = cls.__annotations__.get("params")
        if params_annotation is None:
            return params
        actual_type = params_annotation
        if isinstance(actual_type, types.UnionType):
            if non_none := [a for a in actual_type.__args__ if a is not type(None)]:
                actual_type = non_none[0]
        if isinstance(actual_type, type) and actual_type is not dict:
            with contextlib.suppress(Exception):
                return actual_type(**params)
        return params

    @classmethod
    def new_action(
        cls,
        *,
        mid: int | str,
        page: Page,
        variables: Dict,
        params: ParamsT | None = None,
        timeout: int = 30000,
        input_vars: dict | None = None,
        output_vars: List[str] | None = None,
        action_name: BuiltinActionName | str | None = None,
    ):
        safe_params = cls._convert_params(params or {})
        safe_input = input_vars or {}
        safe_output = output_vars or []
        safe_variables = variables or {}

        kwargs: Dict = {
            "action_id": getattr(cls, "action_id", BuiltinActionType.COMPOSITE),
            "action_type": getattr(cls, "action_type", BuiltinActionType.COMPOSITE),
            "mid": mid,
            "page": page,
            "params": safe_params,
            "timeout": timeout,
            "input_vars": safe_input,
            "output_vars": safe_output,
            "variables": safe_variables,
        }
        if action_name is not None:
            kwargs["_action_name"] = action_name
        return cls(**kwargs)

    @property
    def params_model(self) -> Type[ParamsT]:
        return self.action_type.params_model  # type: ignore [return-value]

    @property
    def result_model(self) -> Type[AllActionResult]:
        """返回该操作类型对应的结果模型"""
        return self.action_type.result_model

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
        """返回动作元数据，由 action_type 构建"""
        return build_action_metadata(self.action_type)

    def add_log(self, message: str):
        """添加日志"""
        self._logs.append(f"[{self._phase.value}] {message}")

    def get_logs(self) -> List[str]:
        return self._logs.copy()

    def clear_logs(self):
        self._logs.clear()

    def validate_params_with_model(self, params: ParamsT) -> tuple[bool, str, ParamsT]:
        """使用模型验证参数"""
        target = params if params is not None else self.params
        if not self.params_model:
            raise ValueError(f"Action with no params_model: {self}")
        try:
            validated = self.params_model.model_validate(target)
            return True, "", validated
        except Exception as e:
            raise ValueError(f"Invalid params: {e}") from e

    def validate_params(self, params: dict | None = None) -> tuple[bool, str | None]:
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

    @abstractmethod
    async def _execute(self) -> ActionResult:
        """
        执行动作（子类必须实现）

        所有参数通过 self 访问，无需传参。
        """
        ...

    async def execute(self) -> ActionResult:
        """执行动作"""
        try:
            action_result = await self._execute()
        except Exception as e:
            action_result = ActionResult(
                success=False,
                error=f"执行异常: {e}",
                action_id=self.action_id,
                action_name=self.action_name,
            )
        self._annotate_error_context(action_result)
        self._merge_output_vars(action_result)
        action_result.variables = {
            k: v for k, v in self.variables.items() if not callable(v)
        }
        return action_result

    def _annotate_error_context(self, action_result: ActionResult) -> None:
        """给失败结果统一补「动作名(动作ID)」上下文前缀。

        各动作内部普遍以裸 ``str(e)`` 上报错误（Playwright / httpx / LangChain
        异常串），单条操作日志看不出是哪个动作、什么场景出的错。在统一出口补前缀，
        复合动作嵌套执行时逐层叠加，可直接定位到最内层失败步骤，例如：
        ``[点击(click)] Timeout 5000ms exceeded ... waiting for locator(".xxx")``
        """
        if action_result.success or not action_result.error:
            return
        action_id = getattr(action_result.action_id, "value", action_result.action_id)
        action_name = getattr(action_result.action_name, "value", action_result.action_name)
        prefix = f"[{action_name or self.action_name}({action_id})]"
        if not action_result.error.startswith(prefix):
            action_result.error = f"{prefix} {action_result.error}"

    def preview(self) -> dict:
        """
        预览模式：使用 result_model 构造模拟返回值并执行变量合并。

        不实际执行操作，仅用于测试变量赋值效果。
        返回包含 action_result 和 variables 的字典。
        """
        result_model = self.result_model
        mock_data = result_model()
        action_result = ActionResult(
            success=True,
            data=mock_data,
            action_id=self.action_id,
            action_name=self.action_name,
        )
        self._merge_output_vars(action_result)
        return {
            "action_result": action_result,
            "variables": self.variables,
        }
