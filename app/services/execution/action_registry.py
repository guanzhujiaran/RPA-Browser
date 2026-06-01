"""
Action Registry - 操作注册表

保留此文件以保持向后兼容。
实际逻辑已移至 unified_registry.py
"""

from app.services.execution.unified_registry import (
    UnifiedActionRegistry,
    unified_action_registry,
)

ActionRegistry = UnifiedActionRegistry
action_registry = unified_action_registry

__all__ = ["ActionRegistry", "action_registry"]
