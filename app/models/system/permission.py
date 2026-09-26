"""
System 模块 - 权限配置模型
"""

from sqlmodel import SQLModel, Field
from typing import List


class PermissionLevelConfig(SQLModel):
    """权限等级配置"""

    level_name: str = Field(description="等级名称，如 level0, level1, root")
    level_value: int = Field(description="等级数值")
    permissions: List[int] = Field(description="该等级拥有的权限列表")
    max_fingerprints: int = Field(
        default=999999, description="该等级允许创建的最大浏览器指纹数量"
    )


class PermissionConfigList(SQLModel):
    """权限配置列表"""

    levels: List[PermissionLevelConfig] = Field(description="所有等级的配置")


class PermissionConfigData(SQLModel):
    """权限配置数据模型（用于JSON序列化）"""

    levels: List[PermissionLevelConfig] = Field(description="所有等级的配置")


class PermissionLevelQuotaUpdate(SQLModel):
    """单个等级的指纹配额更新项。

    只承载「最大指纹数量」：``permissions`` / ``level_value`` 由服务端按磁盘现值回写，
    不接受调用方传值（避免经配额接口变相修改功能权限位）。
    """

    level_name: str = Field(description="等级名称，如 level0 / level6 / root")
    max_fingerprints: int = Field(
        ge=0, description="该等级允许创建的最大浏览器指纹数量（≥0）"
    )


class PermissionQuotaUpdateReq(SQLModel):
    """等级指纹配额更新请求：按 level_name 合并，未传的等级保持现值。"""

    levels: List[PermissionLevelQuotaUpdate] = Field(description="需要更新的等级列表")


class PermissionQuotaResp(SQLModel):
    """等级权限配置响应（含配置文件路径，便于管理端/运维定位实际读写位置）。"""

    levels: List[PermissionLevelConfig] = Field(description="所有等级的配置")
    config_file: str = Field(description="配置文件路径（实际读写位置）")


__all__ = [
    "PermissionLevelConfig",
    "PermissionConfigList",
    "PermissionConfigData",
    "PermissionLevelQuotaUpdate",
    "PermissionQuotaUpdateReq",
    "PermissionQuotaResp",
]
