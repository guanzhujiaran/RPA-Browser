"""
Browser Exec Info Model - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.core.browser_exec 中的模型。
"""

from app.models.core.browser_exec import (
    BrowserExecInfoModel,
    BrowserExecInfoModels,
)

__all__ = [
    "BrowserExecInfoModel",
    "BrowserExecInfoModels",
]
