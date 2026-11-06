# 内置动作汇总表：import 全部内置动作模块触发自注册，并提供兼容查询 API
from app.models.execution.action_params import (
    ActionMetadata,
    BuiltinActionType,
    build_action_metadata,
)
from typing import Type

from app.services.execution.actions.base import (
    BaseAction,
    get_action_class,
    get_registered_action_classes,
)

# import 全部内置动作模块 → 触发各自底部的 register_action_class 自注册
from app.services.execution.actions.interaction import (  # noqa: F401
    ClickAction,
    InputAction,
    ScrollAction,
    WaitAction,
    HoverAction,
    GetTextAction,
    GetWindowAction,
    PressAction,
    ElementStateAction,
    SelectOptionAction,
)
from app.services.execution.actions.navigation import (  # noqa: F401
    NavigateAction,
    NewPageAction,
    WaitForUrlAction,
)
from app.services.execution.actions.wait_for_response import (  # noqa: F401
    WaitForResponseAction,
)
from app.services.execution.actions.page_actions import (  # noqa: F401
    ClosePageAction,
    OpenPageAction,
)
from app.services.execution.actions.screenshot import ScreenshotAction  # noqa: F401
from app.services.execution.actions.llm import LLMAction  # noqa: F401
from app.services.execution.actions.fetch_external_data import (  # noqa: F401
    FetchExternalDataAction,
)
from app.services.execution.actions.mark_resource_result import (  # noqa: F401
    MarkResourceResultAction,
)
from app.services.execution.actions.random_pick import RandomPickAction  # noqa: F401
from app.services.execution.actions.debug import PrintAction  # noqa: F401
from app.services.execution.actions.notify import NotifyAction  # noqa: F401
from app.services.execution.actions.list_utils import (  # noqa: F401
    ShuffleListAction,
    SortListAction,
    PickLineAction,
)
from app.services.execution.actions.bili import BiliReserveAction  # noqa: F401
from app.services.execution.actions.control_flow import (  # noqa: F401
    LoopAction,
    IfElseAction,
)
from typing import Dict  # noqa: F401

# 内置动作映射常量：构建自 base 的注册表快照（各动作模块自注册，单一数据源）。
# key 为 action_id 的字符串 value；StrEnum 与其 value 哈希相等，
# 故 ``MAP.get(BuiltinActionType.CLICK)`` 与 ``MAP.get("click")`` 等价。
BUILTIN_ACTION_MAP: Dict[str, Type[BaseAction]] = get_registered_action_classes()


def get_all_actions_metadata() -> list[ActionMetadata]:
    """获取所有注册的动作元数据"""
    return [build_action_metadata(action_type) for action_type in BuiltinActionType]


def get_action_metadata(action_type: BuiltinActionType) -> ActionMetadata:
    return build_action_metadata(action_type)


__all__ = [
    "BUILTIN_ACTION_MAP",
    "get_action_class",
    "get_all_actions_metadata",
    "get_action_metadata",
    "BuiltinActionType",
]
