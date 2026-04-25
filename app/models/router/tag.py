"""
Router 模块 - 路由标签定义
"""

from enum import Enum


class StrEnum(str, Enum):
    """字符串枚举，兼容 Python 3.10"""
    def __str__(self):
        return str(self.value)


class RouterTag(StrEnum):
    # === 浏览器配置管理 ===
    browser_fingerprint = "浏览器指纹管理"
    browser_plugin = "浏览器插件管理"
    browser_notification = "通知配置管理"
    browser_default_settings = "浏览器默认设置管理"

    # === 浏览器运行时管理 ===
    browser_control = "浏览器实时控制"
    session_management = "会话管理"
    operation_control = "自动化控制"
    action_management = "操作管理"
    video_stream = "视频流"
    plugin_control = "插件控制"
    security_control = "安全控制"
    system_control = "系统管理"
    webrtc_control = "WebRTC 控制"
    session_control = "会话操作"

    # === 系统管理 ===
    system_management = "系统管理"
    admin_management = "管理员管理"


class VersionTag(StrEnum):
    v1 = "v1"


__all__ = ["RouterTag", "VersionTag", "StrEnum"]
