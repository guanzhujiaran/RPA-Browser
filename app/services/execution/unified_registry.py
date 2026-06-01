"""
Unified Action Registry - 统一操作注册表

支持统一数据模型，从 ExecutionRecord 表加载 action 和 plugin。
"""

from typing import Type, Optional, List
from sqlmodel import select

from app.models.database.workflow.unified_models import (
    ExecutionRecord,
    ActionMetadata,
    ActionCategory,
)
from app.utils.depends.session_manager import DatabaseSessionManager

from app.services.execution.actions.base import BaseAction
from app.services.execution.actions.interaction import ClickAction, InputAction, ScrollAction, WaitAction
from app.services.execution.actions.navigation import NavigateAction, NewPageAction
from app.services.execution.actions.screenshot import ScreenshotAction
from app.services.execution.actions.llm import LLMAction
from app.services.execution.actions.control_flow import LoopAction, IfElseAction


class UnifiedActionRegistry:
    """
    统一操作注册表

    统一管理原子动作和组合动作。
    原子动作在代码中注册，组合动作从数据库加载。
    """

    def __init__(self):
        self._builtin_actions: dict[str, Type[BaseAction]] = {
            "click": ClickAction,
            "input": InputAction,
            "navigate": NavigateAction,
            "new_page": NewPageAction,
            "scroll": ScrollAction,
            "wait": WaitAction,
            "screenshot": ScreenshotAction,
            "llm": LLMAction,
            "loop": LoopAction,
            "if_else": IfElseAction,
        }
        self._system_actions: dict[str, Type[BaseAction]] = {}

    def register(self, action_class: Type[BaseAction], action_id: str | None = None):
        """
        注册系统级动作

        Args:
            action_class: 动作类
            action_id: 动作 ID（可选）
        """
        temp_instance = action_class()
        metadata = temp_instance.get_metadata()
        action_id = action_id or metadata.id

        if action_id in self._system_actions:
            raise ValueError(f"动作 ID {action_id} 已存在")

        self._system_actions[action_id] = action_class

    def unregister(self, action_id: str):
        """注销系统级动作"""
        if action_id in self._system_actions:
            del self._system_actions[action_id]

    def get_action(self, action_id: str) -> Type[BaseAction] | None:
        """获取系统级动作类"""
        if action_id in self._builtin_actions:
            return self._builtin_actions[action_id]
        return self._system_actions.get(action_id)

    def create_action(self, action_id: str) -> BaseAction | None:
        """创建系统级动作实例"""
        if action_id in self._builtin_actions:
            return self._builtin_actions[action_id]()
        if action_id in self._system_actions:
            return self._system_actions[action_id]()
        return None

    async def create_action_for_user(
        self, action_id: str, mid: str
    ) -> BaseAction | None:
        """
        为指定用户创建动作实例

        查找顺序：
        1. 内置动作
        2. 系统级动作
        3. 数据库用户动作（组合动作）
        4. 数据库用户插件

        Args:
            action_id: 动作 ID
            mid: 用户 ID

        Returns:
            BaseAction 实例，未找到返回 None
        """
        # 1 & 2: 系统级
        if system_action := self.create_action(action_id):
            return system_action

        # 3: 从数据库加载用户动作
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ExecutionRecord).where(
                    ExecutionRecord.action_id == action_id,
                    ExecutionRecord.mid == int(mid),
                    ExecutionRecord.is_enabled == True,
                )
            )
            record = result.first()

        if record:
            if record.category == ActionCategory.COMPOSITE:
                from app.services.execution.actions.base import CompositeAction

                composite = CompositeAction(
                    action_id=record.action_id,
                    name=record.name,
                    description=record.description,
                    steps=record.steps,
                )
                composite.set_registry(self)
                return composite

            elif record.category == ActionCategory.PLUGIN:
                from app.services.execution.actions.base import PluginAction

                plugin = PluginAction(
                    action_id=record.action_id,
                    name=record.name,
                    hook_type=record.hook_type,
                    description=record.description,
                    steps=record.steps,
                )
                plugin.set_registry(self)
                return plugin

        return None

    def get_all_actions(self) -> list[ActionMetadata]:
        """获取所有系统级动作的元数据"""
        result = []
        result.extend(action_class().get_metadata() for action_class in self._builtin_actions.values())
        result.extend(action_class().get_metadata() for action_class in self._system_actions.values())
        return result

    def get_action_metadata(self, action_id: str) -> ActionMetadata | None:
        """获取系统级动作元数据"""
        if action_id in self._builtin_actions:
            return self._builtin_actions[action_id]().get_metadata()
        if action_id in self._system_actions:
            return self._system_actions[action_id]().get_metadata()
        return None

    def is_builtin_action(self, action_id: str) -> bool:
        """判断是否为内置动作"""
        return action_id in self._builtin_actions

    def is_system_action(self, action_id: str) -> bool:
        """判断是否为系统级动作"""
        return action_id in self._system_actions


unified_action_registry = UnifiedActionRegistry()

action_registry = unified_action_registry
