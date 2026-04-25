from enum import Enum


# 兼容 Python 3.10 的 StrEnum
class StrEnum(str, Enum):
    """字符串枚举，兼容 Python 3.10"""
    def __str__(self):
        return str(self.value)




class RouterTag(StrEnum):
    # === 浏览器配置管理 ===
    browser_fingerprint = "浏览器指纹管理"  # 指纹的 CRUD
    browser_plugin = "浏览器插件管理"  # 插件配置管理
    browser_notification = "通知配置管理"  # 通知渠道配置
    browser_default_settings = "浏览器默认设置管理"  # 用户浏览器默认设置管理

    # === 浏览器运行时管理 ===
    browser_control = "浏览器实时控制"  # 运行时操作控制
    session_management = "会话管理"  # 心跳、状态查询
    operation_control = "自动化控制"  # 暂停/恢复
    action_management = "操作管理"  # 操作执行、插件、工作流
    video_stream = "视频流"  # MJPEG、WebRTC 流
    plugin_control = "插件控制"  # 插件运行时控制
    security_control = "安全控制"  # JS代码安全检查与执行
    system_control = "系统管理"  # 健康检查、统计、清理
    webrtc_control = "WebRTC 控制"
    session_control = "会话操作"
    # === 浏览器配置管理 ===
    # === 系统管理 ===
    system_management = "系统管理"  # 系统监控与维护
    admin_management = "管理员管理"  # 超级管理员功能


class VersionTag(StrEnum):
    v1 = "v1"
