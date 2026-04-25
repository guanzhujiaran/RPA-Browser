"""
Browser Database Models - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.core.browser_info 中的模型。
"""

from app.models.core.browser_info import (
    UserBrowserToken,
    UserBrowserInfoBase,
    UserBrowserServerSideDefaultSetting,
    UserBrowserDefaultSetting,
    UserBrowserInfoWithoutPlugin,
    UserBrowserInfo,
    UserBrowserDefaultSettingRequest,
    UserBrowserDefaultSettingResponse,
)

__all__ = [
    "UserBrowserToken",
    "UserBrowserInfoBase",
    "UserBrowserServerSideDefaultSetting",
    "UserBrowserDefaultSetting",
    "UserBrowserInfoWithoutPlugin",
    "UserBrowserInfo",
    "UserBrowserDefaultSettingRequest",
    "UserBrowserDefaultSettingResponse",
]
