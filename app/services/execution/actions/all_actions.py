# 内置动作映射常量

from app.models.database.workflow.models import ActionMetadata
from app.services.execution.actions.base import BaseAction
from app.models.database.workflow.models import BuiltinActionType
from app.services.execution.actions.interaction import (
    ClickAction, InputAction, ScrollAction, WaitAction, HoverAction,
)
from app.services.execution.actions.navigation import NavigateAction, NewPageAction
from app.services.execution.actions.screenshot import ScreenshotAction
from app.services.execution.actions.llm import LLMAction
from app.services.execution.actions.control_flow import LoopAction, IfElseAction
from app.models.database.workflow.models import BUILTIN_ACTION_PARAMS_MAP
from typing import Dict, Type
BUILTIN_ACTION_MAP: Dict[str, Type[BaseAction]] = {
    BuiltinActionType.CLICK: ClickAction,
    BuiltinActionType.INPUT: InputAction,
    BuiltinActionType.SCROLL: ScrollAction,
    BuiltinActionType.WAIT: WaitAction,
    BuiltinActionType.HOVER: HoverAction,
    BuiltinActionType.NAVIGATE: NavigateAction,
    BuiltinActionType.NEW_PAGE: NewPageAction,
    BuiltinActionType.SCREENSHOT: ScreenshotAction,
    BuiltinActionType.LLM: LLMAction,
    BuiltinActionType.LOOP: LoopAction,
    BuiltinActionType.IF_ELSE: IfElseAction,
}



def get_action_class(action_id: str) -> Type[BaseAction] | None:
    """获取动作类"""
    return BUILTIN_ACTION_MAP.get(action_id)


def get_all_actions_metadata() -> list[Type[BaseAction]]:
    """获取所有注册的动作类"""
    return list(BUILTIN_ACTION_MAP.values())


def get_action_metadata(action_type: BuiltinActionType) -> ActionMetadata | None:
    return BUILTIN_ACTION_MAP.get(action_type).metadata


__all__ = [
    "BUILTIN_ACTION_MAP",
    "BUILTIN_ACTION_PARAMS_MAP",
    "get_action_class",
    "get_all_actions_metadata",
    "get_action_metadata",
]
