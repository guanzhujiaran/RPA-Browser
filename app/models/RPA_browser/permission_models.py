"""权限配置模型"""

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
