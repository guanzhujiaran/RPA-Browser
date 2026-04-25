"""
Plugin 模块 - 插件枚举定义

向后兼容：从 core.fingerprint 重新导出 LogPluginLogLevelEnum
"""

from app.models.core.fingerprint import LogPluginLogLevelEnum

__all__ = ["LogPluginLogLevelEnum"]
