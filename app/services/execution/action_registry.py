"""
操作注册表系统

核心设计:
1. 所有浏览器操作都通过 ActionRegistry 注册
2. 操作可以指定前置条件、后置处理
3. 支持操作链：多个操作可以组合成一个工作流
4. 操作执行结果可以传递给下一个操作
"""

from typing import Type
from sqlmodel import select

# 从模型层导入
from app.models.database.workflow.models import (
    CustomActionModel,
    ActionMetadata,
)
from app.utils.depends.session_manager import DatabaseSessionManager

# 从 actions 模块导入所有 Action 类
from app.services.execution.actions.base import BaseAction
from app.services.execution.actions.interaction import ClickAction, InputAction, ScrollAction, WaitAction
from app.services.execution.actions.navigation import NavigateAction, NewPageAction
from app.services.execution.actions.screenshot import ScreenshotAction
from app.services.execution.actions.llm import LLMAction
from app.services.execution.actions.control_flow import LoopAction, IfElseAction, CompositeAction


class ActionRegistry:
    """
    操作注册表

    管理所有可用操作的注册和执行。
    - 内置操作 (_builtin_actions) 和系统级自定义操作 (_actions) 全局共享。
    - 用户自定义组合操作 (_user_composite_actions) 按 mid 隔离，仅作缓存，
      执行时通过 create_action_for_user 按需从数据库加载。
    """

    def __init__(self):
        self._actions: dict[str, Type[BaseAction]] = {}
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

    def register(self, action_class: Type[BaseAction], action_id: str | None = None):
        """注册系统级自定义操作（全局共享，非用户隔离）"""
        temp_instance = action_class()
        metadata = temp_instance.get_metadata()
        action_id = action_id or metadata.id

        if action_id in self._actions:
            raise ValueError(f"操作 ID {action_id} 已存在")

        self._actions[action_id] = action_class

    def unregister(self, action_id: str):
        """注销系统级自定义操作"""
        if action_id in self._actions:
            del self._actions[action_id]

    def get_action(self, action_id: str) -> Type[BaseAction] | None:
        """获取系统级操作类"""
        return self._actions.get(action_id)

    # ------------------------------------------------------------------
    # 操作实例创建
    # ------------------------------------------------------------------

    def create_action(self, action_id: str) -> BaseAction | None:
        """创建系统级操作实例"""
        if action_id in self._builtin_actions:
            return self._builtin_actions[action_id]()
        return self._actions[action_id]() if action_id in self._actions else None

    async def create_action_for_user(
        self, action_id: str, mid: str
    ) -> BaseAction | None:
        """
        为指定用户创建操作实例。

        查找顺序：
        1. 内置操作
        2. 系统级自定义操作
        3. 数据库用户自定义操作

        Args:
            action_id: 操作ID
            mid: 用户 mid

        Returns:
            BaseAction 实例，未找到返回 None
        """
        # 1 & 2: 系统级
        if system_action:= self.create_action(action_id):
            return system_action

        # 3: 从数据库直接读取（不缓存）

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CustomActionModel).where(
                    CustomActionModel.action_id == action_id,
                    CustomActionModel.mid == mid,
                    CustomActionModel.is_enabled == True,
                )
            )

        if (model := result.first()) and (model.is_composite or model.get_steps()):
            composite = CompositeAction(
                action_id=model.action_id,
                name=model.name,
                description=model.description,
                steps=model.get_steps(),
            )
            composite.set_registry(self)
            return composite

        return None

    # ------------------------------------------------------------------
    # 元数据查询
    # ------------------------------------------------------------------

    def get_all_actions(self) -> list[ActionMetadata]:
        """获取所有系统级操作的元数据（内置 + 系统自定义）"""
        result = []
        result.extend(action_class().get_metadata() for action_class in self._builtin_actions.values())
        result.extend(action_class().get_metadata() for action_class in self._actions.values())
        return result

    def get_action_metadata(self, action_id: str) -> ActionMetadata | None:
        """获取系统级操作元数据"""
        if action_id in self._builtin_actions:
            return self._builtin_actions[action_id]().get_metadata()
        if action_id in self._actions:
            return self._actions[action_id]().get_metadata()
        return None


# 全局操作注册表（仅含系统级操作，不含任何用户数据）
action_registry = ActionRegistry()
