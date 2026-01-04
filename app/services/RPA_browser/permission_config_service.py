"""权限配置管理服务 - 基于JSON文件存储"""

import json
from pathlib import Path
from loguru import logger
from typing import List
from app.models.RPA_browser.permission_models import (
    PermissionLevelConfig,
    PermissionConfigList,
    PermissionConfigData,
)
import aiofiles


class PermissionConfigService:
    """权限配置服务"""

    # 配置文件路径
    CONFIG_FILE = Path(__file__).parent.parent.parent / "data" / "permissions.json"

    # 默认权限配置
    DEFAULT_CONFIG: List[PermissionLevelConfig] = [
        PermissionLevelConfig(
            level_name="level0",
            level_value=0,
            permissions=[0, 1, 2, 3, 4, 5, 6],
            max_fingerprints=3,
        ),
        PermissionLevelConfig(
            level_name="level1",
            level_value=1,
            permissions=[1, 2, 3, 4, 5, 6],
            max_fingerprints=10,
        ),
        PermissionLevelConfig(
            level_name="level2",
            level_value=2,
            permissions=[2, 3, 4, 5, 6],
            max_fingerprints=20,
        ),
        PermissionLevelConfig(
            level_name="level3",
            level_value=3,
            permissions=[3, 4, 5, 6],
            max_fingerprints=50,
        ),
        PermissionLevelConfig(
            level_name="level4",
            level_value=4,
            permissions=[4, 5, 6],
            max_fingerprints=100,
        ),
        PermissionLevelConfig(
            level_name="level5", level_value=5, permissions=[5, 6], max_fingerprints=200
        ),
        PermissionLevelConfig(
            level_name="level6", level_value=6, permissions=[6], max_fingerprints=500
        ),
        PermissionLevelConfig(
            level_name="root", level_value=99, permissions=[6], max_fingerprints=999999
        ),
    ]

    @classmethod
    def _ensure_config_dir(cls):
        """确保配置目录存在"""
        cls.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    async def _get_config_data(cls) -> PermissionConfigData:
        """从JSON文件读取配置数据"""

        if not cls.CONFIG_FILE.exists():
            logger.info(f"权限配置文件不存在，创建默认配置: {cls.CONFIG_FILE}")
            cls._ensure_config_dir()
            await cls._save_default_config()

        async with aiofiles.open(cls.CONFIG_FILE, "r", encoding="utf-8") as f:
            content = await f.read()
            data = json.loads(content)
            return PermissionConfigData(levels=[PermissionLevelConfig(**item) for item in data.get("levels", [])])

    @classmethod
    async def _save_config_data(cls, config_data: PermissionConfigData):
        """保存配置数据到JSON文件"""
        cls._ensure_config_dir()
        async with aiofiles.open(cls.CONFIG_FILE, "w", encoding="utf-8") as f:
            await f.write(config_data.model_dump_json(indent=2, exclude_none=True))
        logger.info(f"权限配置已保存: {cls.CONFIG_FILE}")

    @classmethod
    async def _save_default_config(cls):
        """保存默认配置"""
        config_data = PermissionConfigData(levels=cls.DEFAULT_CONFIG)
        await cls._save_config_data(config_data)

    @classmethod
    async def get_permissions(cls) -> PermissionConfigList:
        """获取所有权限配置"""
        try:
            config_data = await cls._get_config_data()
            return PermissionConfigList(levels=config_data.levels)
        except Exception as e:
            logger.error(f"读取权限配置失败: {e}")
            return PermissionConfigList(levels=cls.DEFAULT_CONFIG)

    @classmethod
    async def update_permissions(cls, config: PermissionConfigList) -> bool:
        """更新权限配置"""
        try:
            config_data = PermissionConfigData(levels=config.levels)
            await cls._save_config_data(config_data)
            logger.info(f"权限配置已更新，共 {len(config.levels)} 个等级")
            return True
        except Exception as e:
            logger.error(f"更新权限配置失败: {e}")
            return False

    @classmethod
    async def reset_to_default(cls) -> bool:
        """重置为默认配置"""
        try:
            await cls._save_default_config()
            logger.info("权限配置已重置为默认值")
            return True
        except Exception as e:
            logger.error(f"重置权限配置失败: {e}")
            return False

    @classmethod
    async def get_permissions_by_level(cls, level_name: str) -> List[int] | None:
        """根据等级名称获取权限列表"""
        config = await cls.get_permissions()
        for level_config in config.levels:
            if level_config.level_name == level_name:
                return level_config.permissions
        return None

    @classmethod
    async def get_permissions_by_level_value(cls, level_value: int) -> List[int] | None:
        """根据等级数值获取权限列表"""
        config = await cls.get_permissions()
        for level_config in config.levels:
            if level_config.level_value == level_value:
                return level_config.permissions
        return None

    @classmethod
    async def get_max_fingerprints_by_level(cls, level_value: int) -> int:
        """根据等级数值获取允许创建的最大浏览器指纹数量"""
        config = await cls.get_permissions()
        for level_config in config.levels:
            if level_config.level_value == level_value:
                return level_config.max_fingerprints
        return 0  # 未找到等级则不允许创建
