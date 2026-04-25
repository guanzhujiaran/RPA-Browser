"""
Plugin Model - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.plugin.models 中的模型。
"""

from app.models.plugin.models import (
    PluginBaseModel,
    PluginBaseModelWithoutMid,
    LogPluginModel,
    PageLimitPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel,
    LogPluginCreate,
    PageLimitPluginCreate,
    RandomWaitPluginCreate,
    RetryPluginCreate,
)
from app.models.plugin.enums import LogPluginLogLevelEnum

__all__ = [
    "PluginBaseModel",
    "PluginBaseModelWithoutMid",
    "LogPluginModel",
    "PageLimitPluginModel",
    "RandomWaitPluginModel",
    "RetryPluginModel",
    "LogPluginCreate",
    "PageLimitPluginCreate",
    "RandomWaitPluginCreate",
    "RetryPluginCreate",
    "LogPluginLogLevelEnum",
]
