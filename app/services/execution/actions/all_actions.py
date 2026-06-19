# 内置动作映射常量
from app.models.execution.action_params import ActionMetadata, BuiltinActionType
from typing import Type
from app.services.execution.actions.interaction import (
    ClickAction, InputAction, ScrollAction, WaitAction, HoverAction, GetTextAction,
)
from app.services.execution.actions.navigation import NavigateAction, NewPageAction
from app.services.execution.actions.screenshot import ScreenshotAction
from app.services.execution.actions.llm import LLMAction
from app.services.execution.actions.control_flow import LoopAction, IfElseAction
from typing import Dict
AllActionType = ClickAction | InputAction | ScrollAction | WaitAction | HoverAction | GetTextAction | NavigateAction | NewPageAction | ScreenshotAction | LLMAction | LoopAction | IfElseAction
BUILTIN_ACTION_MAP: Dict[str, Type[AllActionType]] = {
    BuiltinActionType.CLICK: ClickAction,
    BuiltinActionType.INPUT: InputAction,
    BuiltinActionType.SCROLL: ScrollAction,
    BuiltinActionType.WAIT: WaitAction,
    BuiltinActionType.HOVER: HoverAction,
    BuiltinActionType.GET_TEXT: GetTextAction,
    BuiltinActionType.NAVIGATE: NavigateAction,
    BuiltinActionType.NEW_PAGE: NewPageAction,
    BuiltinActionType.SCREENSHOT: ScreenshotAction,
    BuiltinActionType.LLM: LLMAction,
    BuiltinActionType.LOOP: LoopAction,
    BuiltinActionType.IF_ELSE: IfElseAction,
}


def get_action_class(action_id: str) -> Type[AllActionType] | None:
    """获取动作类"""
    return BUILTIN_ACTION_MAP.get(action_id)


def get_all_actions_metadata() -> list[ActionMetadata]:
    """获取所有注册的动作元数据"""
    return [action_type.metadata for action_type in BuiltinActionType]


def get_action_metadata(action_type: BuiltinActionType) -> ActionMetadata:
    return action_type.metadata


__all__ = [
    "BUILTIN_ACTION_MAP",
    "get_action_class",
    "get_all_actions_metadata",
    "get_action_metadata",
    "BuiltinActionType",
    "AllActionType"
]
