import sys

# Python 3.10 兼容性：StrEnum 在 3.11+ 中引入
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum
    class StrEnum(str, Enum):
        """Python 3.10 兼容的 StrEnum"""
        pass


class RouterTag(StrEnum):
    # === 浏览器配置管理 ===
    browser_fingerprint = "浏览器指纹管理"  # 指纹的 CRUD
    browser_notification = "通知配置管理"  # 通知渠道配置
    browser_default_settings = "浏览器默认设置管理"  # 用户浏览器默认设置管理

    # === 浏览器运行时管理 ===
    browser_control = "浏览器实时控制"  # 运行时操作控制
    session_management = "会话管理"  # 心跳、状态查询
    operation_control = "自动化控制"  # 暂停/恢复
    action_management = "自定义操作管理"  # Custom Action CRUD
    workflow_management = "工作流管理"  # Workflow CRUD 和执行
    plugin_management = "插件挂载管理"  # Plugin 生命周期钩子配置
    webrtc_video_stream = "WebRTC 视频流"  # WebRTC 超低延迟实时画面传输
    session_control = "浏览器会话控制"
    execution_engine = "执行引擎"  # 提供浏览器操作的执行
    # === 系统管理 ===
    community_management = "社区互动管理"  # 公开资源浏览、点赞、举报、Fork
    # === 系统管理 ===
    admin_management = "管理员管理"  # 超级管理员功能


class VersionTag(StrEnum):
    v1 = "v1"
