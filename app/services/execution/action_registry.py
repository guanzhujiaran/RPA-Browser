"""
Action Registry - 动作注册表

简化设计：只管理原子动作，从数据库加载组合动作
"""

from typing import Type, Optional, Dict, Any
from sqlmodel import select

from app.models.database.workflow.models import CustomAction, UserPlugin
from app.utils.depends.session_manager import DatabaseSessionManager

from app.services.execution.actions.base import BaseAction, CompositeAction, PluginAction


class ActionRegistry:
    """
    动作注册表
    
    管理所有可用动作：
    1. 内置原子动作（代码注册）
    2. 用户组合动作（从数据库加载）
    3. 用户插件（从数据库加载，合并到 CustomAction）
    """
    
    def __init__(self):
        self._actions: Dict[str, Type[BaseAction]] = {}
        self._register_builtin_actions()
    
    def _register_builtin_actions(self):
        """注册内置动作"""
        from app.services.execution.actions.interaction import (
            ClickAction, InputAction, ScrollAction, WaitAction, HoverAction,
        )
        from app.services.execution.actions.navigation import NavigateAction, NewPageAction
        from app.services.execution.actions.screenshot import ScreenshotAction
        from app.services.execution.actions.llm import LLMAction
        from app.services.execution.actions.control_flow import LoopAction, IfElseAction
        
        builtin_actions = {
            "click": ClickAction,
            "input": InputAction,
            "scroll": ScrollAction,
            "wait": WaitAction,
            "hover": HoverAction,
            "navigate": NavigateAction,
            "new_page": NewPageAction,
            "screenshot": ScreenshotAction,
            "llm": LLMAction,
            "loop": LoopAction,
            "if_else": IfElseAction,
        }
        
        for action_id, action_class in builtin_actions.items():
            self.register(action_class, action_id)
    
    def register(self, action_class: Type[BaseAction], action_id: Optional[str] = None):
        """注册动作"""
        temp_instance = action_class()
        action_id = action_id or temp_instance.action_id or action_class.get_action_id()
        
        if action_id in self._actions:
            raise ValueError(f"动作 ID {action_id} 已存在")
        
        self._actions[action_id] = action_class
    
    def unregister(self, action_id: str):
        """注销动作"""
        if action_id in self._actions:
            del self._actions[action_id]
    
    def get_action_class(self, action_id: str) -> Optional[Type[BaseAction]]:
        """获取动作类"""
        return self._actions.get(action_id)
    
    def create_action(self, action_id: str) -> Optional[BaseAction]:
        """创建动作实例（仅限内置动作）"""
        action_class = self._actions.get(action_id)
        if action_class:
            return action_class()
        return None
    
    async def create_action_for_user(self, action_id: str, mid: str) -> Optional[BaseAction]:
        """
        为用户创建动作实例
        
        查找顺序：
        1. 内置动作
        2. 用户自定义组合动作（CustomAction）
        3. 用户插件（UserPlugin，合并为带 hook_type 的组合动作）
        """
        # 1. 内置动作
        if action_class := self._actions.get(action_id):
            return action_class()
        
        # 2. 从数据库加载用户动作
        async with DatabaseSessionManager.async_session() as session:
            # 查找 CustomAction
            result = await session.exec(
                select(CustomAction).where(
                    CustomAction.action_id == action_id,
                    CustomAction.mid == int(mid),
                    CustomAction.is_enabled == True,
                )
            )
            custom_action = result.first()
        
        if custom_action:
            composite = CompositeAction(
                action_id=custom_action.action_id,
                action_name=custom_action.name,
                steps=custom_action.steps or [],
            )
            composite.set_registry(self)
            return composite
        
        # 3. 查找 UserPlugin（插件合并到 CustomAction）
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserPlugin).where(
                    UserPlugin.plugin_id == action_id,
                    UserPlugin.mid == int(mid),
                    UserPlugin.is_enabled == True,
                )
            )
            user_plugin = result.first()
        
        if user_plugin:
            # 查找关联的 CustomAction
            async with DatabaseSessionManager.async_session() as session:
                result = await session.exec(
                    select(CustomAction).where(
                        CustomAction.action_id == user_plugin.custom_action_id,
                        CustomAction.is_enabled == True,
                    )
                )
                linked_action = result.first()
            
            if linked_action:
                plugin = PluginAction(
                    action_id=user_plugin.plugin_id,
                    action_name=user_plugin.name,
                    hook_type=user_plugin.hook_type,
                    steps=linked_action.steps or [],
                )
                plugin.set_registry(self)
                return plugin
        
        return None
    
    def list_builtin_actions(self) -> list[str]:
        """列出内置动作"""
        return list(self._actions.keys())
    
    def is_builtin(self, action_id: str) -> bool:
        """判断是否为内置动作"""
        return action_id in self._actions


# 全局注册表
action_registry = ActionRegistry()
