"""
Browser Session Model - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.runtime.session 中的模型。
"""

from app.models.runtime.session import (
    BrowserSessionBaseParams,
    BrowserSessionGetParams,
    BrowserSessionCreateParams,
    BrowserSessionAllRemoveParams,
    BrowserSessionRemoveParams,
    SessionCreateParams,
    SessionCloseResponse,
    SessionAllCloseResponse,
)

__all__ = [
    "BrowserSessionBaseParams",
    "BrowserSessionGetParams",
    "BrowserSessionCreateParams",
    "BrowserSessionAllRemoveParams",
    "BrowserSessionRemoveParams",
    "SessionCreateParams",
    "SessionCloseResponse",
    "SessionAllCloseResponse",
]
