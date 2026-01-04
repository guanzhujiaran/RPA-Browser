"""
Mid相关依赖注入函数
"""
from dataclasses import dataclass
from enum import IntEnum
from typing import List
from fastapi import Header
from app.models.exceptions.base_exception import (
    NotLoggedInException,
    InvalidUIDException,
    InvalidMidFormatException,
)


class UserLevel(IntEnum):
    """用户等级枚举"""
    LEVEL_0 = 0
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4
    LEVEL_5 = 5
    LEVEL_6 = 6
    ROOT = 99  # 特殊root权限

    @classmethod
    def from_string(cls, level_str: str) -> "UserLevel":
        """从字符串解析等级"""
        level_map = {
            "level0": cls.LEVEL_0,
            "level1": cls.LEVEL_1,
            "level2": cls.LEVEL_2,
            "level3": cls.LEVEL_3,
            "level4": cls.LEVEL_4,
            "level5": cls.LEVEL_5,
            "level6": cls.LEVEL_6,
            "root": cls.ROOT,
        }
        return level_map.get(level_str.lower(), cls.LEVEL_0)  # 默认为最低等级


class Permission(IntEnum):
    """权限枚举"""
    PERMISSION_0 = 0
    PERMISSION_1 = 1
    PERMISSION_2 = 2
    PERMISSION_3 = 3
    PERMISSION_4 = 4
    PERMISSION_5 = 5
    PERMISSION_6 = 6


class LevelPermissions:
    """等级权限映射"""
    LEVEL_0: List[int] = [0, 1, 2, 3, 4, 5, 6]
    LEVEL_1: List[int] = [1, 2, 3, 4, 5, 6]
    LEVEL_2: List[int] = [2, 3, 4, 5, 6]
    LEVEL_3: List[int] = [3, 4, 5, 6]
    LEVEL_4: List[int] = [4, 5, 6]
    LEVEL_5: List[int] = [5, 6]
    LEVEL_6: List[int] = [6]
    ROOT: List[int] = [6]

    @staticmethod
    def get_permissions(level: int) -> List[int]:
        """获取指定等级的权限列表"""
        if level == 0:
            return LevelPermissions.LEVEL_0
        elif level == 1:
            return LevelPermissions.LEVEL_1
        elif level == 2:
            return LevelPermissions.LEVEL_2
        elif level == 3:
            return LevelPermissions.LEVEL_3
        elif level == 4:
            return LevelPermissions.LEVEL_4
        elif level == 5:
            return LevelPermissions.LEVEL_5
        elif level == 6:
            return LevelPermissions.LEVEL_6
        elif level >= 99:  # root
            return LevelPermissions.ROOT
        else:
            return LevelPermissions.LEVEL_0  # 默认最低权限


@dataclass
class AuthInfo:
    """认证信息"""
    mid: int
    level: int


def get_auth_info_from_header(
    x_bili_mid: str = Header(...),
    x_bili_level: str = Header(...)
) -> AuthInfo:
    """
    从请求头中获取认证信息并验证用户是否已登录

    Args:
        x_bili_mid: 请求头中的x-bili-mid字段
        x_bili_level: 请求头中的x-bili-level字段（字符串格式，如 "level0", "level1"）

    Returns:
        AuthInfo: 包含mid和level的认证信息对象

    Raises:
        NotLoggedInException: 当用户未登录时抛出
        InvalidUIDException: 当用户ID无效时抛出
        InvalidMidFormatException: 当mid格式无效时抛出
    """
    if not x_bili_mid:
        raise NotLoggedInException()

    # 验证mid是否为有效的数字字符串并转换为int
    try:
        mid_int = int(x_bili_mid)
        # 验证mid是否有效（大于0）
        if mid_int <= 0:
            raise InvalidUIDException()
    except ValueError:
        raise InvalidMidFormatException()

    # 解析level字符串（如 "level0" -> UserLevel.LEVEL_0）
    level_enum = UserLevel.from_string(x_bili_level) if x_bili_level else UserLevel.LEVEL_0
    level_int = level_enum.value

    return AuthInfo(mid=mid_int, level=level_int)