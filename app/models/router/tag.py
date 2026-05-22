import sys

# Python 3.10 兼容性：StrEnum 在 3.11+ 中引入
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum
    class StrEnum(str, Enum):
        """Python 3.10 兼容的 StrEnum"""
        pass

"""
Router 模块 - 路由标签定义
"""


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
    action_management = "自定义操作管理"
    workflow_management = "工作流管理"
    plugin_management = "插件挂载管理"
    video_stream = "视频流"
    plugin_control = "插件控制"
    security_control = "安全控制"
    system_control = "系统管理"
    session_control = "会话操作"
    community_management = "社区互动管理"

    # === 系统管理 ===
    system_management = "系统管理"
    admin_management = "管理员管理"


class VersionTag(StrEnum):
    v1 = "v1"


__all__ = ["RouterTag", "VersionTag", "StrEnum"]
