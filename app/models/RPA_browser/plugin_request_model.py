"""
Plugin Request Model - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.plugin.request_models 中的模型。
"""

from app.models.plugin.request_models import (
    PluginCreateRequest,
    PluginGetRequest,
    PluginListRequest,
    PluginDeleteRequest,
    PluginUpdateRequest,
    PluginResponse,
    LogPluginResponse,
    PageLimitPluginResponse,
    RandomWaitPluginResponse,
    RetryPluginResponse,
    PluginDictResponse,
)

__all__ = [
    "PluginCreateRequest",
    "PluginGetRequest",
    "PluginListRequest",
    "PluginDeleteRequest",
    "PluginUpdateRequest",
    "PluginResponse",
    "LogPluginResponse",
    "PageLimitPluginResponse",
    "RandomWaitPluginResponse",
    "RetryPluginResponse",
    "PluginDictResponse",
]
