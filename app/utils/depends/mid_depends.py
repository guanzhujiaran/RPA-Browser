"""
Mid相关依赖注入函数（兼容转发层）

认证逻辑已统一迁移至 bili_common 公共包（bili_common.deps.auth）。
本模块保留转发，避免改动既有的 import 路径。
"""

from bili_common.deps.auth import (
    AuthInfo,
    UserRole,
    UserLevel,
    Permission,
    LevelPermissions,
    get_auth_info_from_header,
)
from app.models.common.exceptions.base_exception import (
    NotLoggedInException,
    InvalidUIDException,
    InvalidMidFormatException,
)

__all__ = [
    "AuthInfo",
    "UserRole",
    "UserLevel",
    "Permission",
    "LevelPermissions",
    "get_auth_info_from_header",
    "NotLoggedInException",
    "InvalidUIDException",
    "InvalidMidFormatException",
]
