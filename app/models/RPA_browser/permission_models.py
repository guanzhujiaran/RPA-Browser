"""
Permission Models - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.system.permission 中的模型。
"""

from app.models.system.permission import (
    PermissionLevelConfig,
    PermissionConfigList,
    PermissionConfigData,
)

__all__ = [
    "PermissionLevelConfig",
    "PermissionConfigList",
    "PermissionConfigData",
]
