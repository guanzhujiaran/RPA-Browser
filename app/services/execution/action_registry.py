"""
操作注册中心

负责管理内置操作和用户自定义操作的注册、查找。
"""
from typing import Any, Dict

from sqlmodel import select

from app.models.database.workflow.models import (
    BuiltinActionType,
    CompositeActionModel,
)
from app.models.execution.action_params import ActionMetadata
from app.services.execution.actions.all_actions import (
    BUILTIN_ACTION_MAP,
    get_action_class,
    get_all_actions_metadata,
)
from app.services.execution.actions.base import BaseAction
from app.utils.depends.session_manager import DatabaseSessionManager


class ActionRegistry:
    """操作注册中心 - 管理内置操作和用户自定义操作"""

    def __init__(self):
        self._builtin_map: dict[str, type[BaseAction]] = dict(BUILTIN_ACTION_MAP)

    async def get_action_class_for_user(self, action_id: str) -> type[BaseAction] | None:
        """
        获取操作类（先查内置，再查用户自定义）

        Args:
            action_id: 操作标识（内置操作为 BuiltinActionType 值，自定义操作为 ca_xxx）
            mid: 用户 mid

        Returns:
            操作类，未找到返回 None
        """
        # 1. 先查内置操作
        builtin = get_action_class(action_id)
        if builtin:
            return builtin

        # 2. 再查用户自定义操作（复合操作）
        from app.services.execution.actions.control_flow import CompositeAction

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel).where(
                    CompositeActionModel.action_id == action_id
                )
            )
            custom = result.first()
            if custom:
                return CompositeAction

        return None

    async def get_custom_action_steps(self, action_id: str) -> list[Dict] | None:
        """
        获取自定义操作的步骤列表（已确保 action_type 字段存在）

        Args:
            action_id: 操作标识

        Returns:
            步骤列表，不是自定义操作返回 None
        """
        from app.models.execution.action_params import _ensure_action_type

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel).where(
                    CompositeActionModel.action_id == action_id
                )
            )
            custom = result.first()
            if custom:
                steps = custom.steps  # List[WorkflowStep] from JSON, may be raw dicts
                # 确保每个 step dict 都有 action_type 字段
                return [_ensure_action_type(s) if isinstance(s, dict) else s for s in steps]
        return None

    def get_action_metadata(self, action_id: str) -> ActionMetadata | None:
        """获取操作元数据"""
        from app.models.execution.action_params import BuiltinActionType as BT
        try:
            return BT(action_id).metadata
        except ValueError:
            return None

    def get_all_action_metadatas(self) -> list[ActionMetadata]:
        """获取所有内置操作的元数据"""
        return get_all_actions_metadata()


# 全局注册中心实例
action_registry = ActionRegistry()