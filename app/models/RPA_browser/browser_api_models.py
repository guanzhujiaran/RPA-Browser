"""
Browser API Models - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.runtime.api 中的模型。
"""

from app.models.runtime.api import *

__all__ = [
    "BrowserFingerprintCreateParams",
    "BrowserFingerprintUpdateParams",
    "BrowserFingerprintUpsertParams",
    "BrowserFingerprintQueryParams",
    "BrowserFingerprintDeleteParams",
    "BrowserFingerprintListParams",
    "BrowserFingerprintRenameParams",
    "BrowserFingerprintCreateResp",
    "BrowserFingerprintUpdateResp",
    "BrowserFingerprintDeleteResp",
    "BrowserFingerprintRenameResp",
    "BrowserFingerprintQueryResp",
    "BrowserOperationOpenUrlParams",
    "BrowserOperationScreenshotParams",
    "BrowserOperationReleaseParams",
    "BrowserOperationOpenUrlResp",
    "BrowserOperationScreenshotResp",
    "BrowserOperationReleaseResp",
    "UserBrowserInfoCreateParams",
    "UserBrowserInfoUpsertParams",
    "UserBrowserInfoCreateResp",
    "UserBrowserInfoCountParams",
    "UserBrowserInfoListParams",
    "UserBrowserInfoReadParams",
    "UserBrowserInfoReadResp",
    "UserBrowserInfoUpdateParams",
    "UserBrowserInfoUpdateResp",
    "UserBrowserInfoDeleteParams",
    "UserBrowserInfoDeleteResp",
    "BrowserOpenUrlParams",
    "BrowserOpenUrlResp",
    "BrowserScreenshotParams",
    "BrowserScreenshotResp",
    "BrowserReleaseParams",
    "BrowserReleaseResp",
]
