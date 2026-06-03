"""
Action Registry - 动作注册表

简化设计：只管理原子动作，从数据库加载组合动作
"""

from app.services.execution.actions.all_actions import BUILTIN_ACTION_MAP
from app.services.execution.actions.control_flow import CompositeAction
from typing import Type, Dict
from app.services.execution.actions.base import BaseAction


class ActionRegistry:
    """
    动作注册表

    管理所有可用动作：
    1. 内置原子动作（代码注册）
    2. 用户组合动作（从数据库加载）
    3. 用户插件（从数据库加载，合并到 CompositeAction）
    """

    def __init__(self):
        self._actions: Dict[str, Type[BaseAction]] = dict(BUILTIN_ACTION_MAP)

    def register(self, action_class: Type[BaseAction], action_id: str | None = None):
        """注册动作类"""
        action_id = action_id or action_class.action_id

        if action_id in self._actions:
            raise ValueError(f"动作 ID {action_id} 已存在")

        self._actions[action_id] = action_class

    def unregister(self, action_id: str):
        """注销动作"""
        if action_id in self._actions:
            del self._actions[action_id]



    def is_builtin(self, action_id: str) -> bool:
        """判断是否为内置动作"""
        return action_id in BUILTIN_ACTION_MAP

    async def get_action_class_for_user(self, action_id: str, mid: int) -> Type[BaseAction] | None:
        """
        为用户获取动作类

        查找顺序：
        1. 内置动作
        2. 用户自定义组合动作（CompositeAction）
        3. 用户插件（UserPlugin，合并为带 hook_type 的组合动作）

        Returns:
            动作类，调用者需要自行实例化
        """
        # 1. 内置动作
        if action_class := self._actions.get(action_id):
            return action_class
        # 不是内置动作的就全部都是组合动作
        return CompositeAction

    def list_builtin_actions(self) -> list[str]:
        """列出内置动作"""
        return list(BUILTIN_ACTION_MAP.keys())

    def get_all_actions_metadata(self) -> list[Type[BaseAction]]:
        """获取所有注册的动作类"""
        return list(self._actions.values())


# 全局注册表
action_registry = ActionRegistry()
