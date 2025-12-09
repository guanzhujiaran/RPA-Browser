from enum import StrEnum


class RouterTag(StrEnum):
    browser_fingerprint = "浏览器指纹"
    browser_control = "浏览器控制"
    plugin_management = "插件管理"
    notification_management = "通知管理"


class VersionTag(StrEnum):
    v1 = "v1"