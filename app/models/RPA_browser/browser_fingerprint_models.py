"""
Browser Fingerprint Models - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.core.fingerprint 中的模型。
"""

from app.models.core.fingerprint import (
    PlatformEnum,
    BrowserEnum,
    Int32,
    BaseFingerprintBrowserInitParams,
)

__all__ = [
    "PlatformEnum",
    "BrowserEnum",
    "Int32",
    "BaseFingerprintBrowserInitParams",
]
