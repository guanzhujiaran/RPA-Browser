from enum import Enum


# 兼容 Python 3.10 的 StrEnum
class StrEnum(str, Enum):
    """字符串枚举，兼容 Python 3.10"""
    def __str__(self):
        return str(self.value)




class RouterTag(StrEnum):
    # === 浏览器配置管理 ===
    browser_fingerprint = "浏览器指纹管理"  # 指纹的 CRUD
    browser_notification = "通知配置管理"  # 通知渠道配置
    browser_default_settings = "浏览器默认设置管理"  # 用户浏览器默认设置管理

    # === 浏览器运行时管理 ===
    browser_control = "浏览器实时控制"  # 运行时操作控制
    session_management = "会话管理"  # 心跳、状态查询
    operation_control = "自动化控制"  # 暂停/恢复
    action_management = "操作管理"  # 操作执行、插件、工作流
    webrtc_video_stream = "WebRTC 视频流"  # WebRTC 超低延迟实时画面传输
    session_control = "浏览器会话控制"
    # === 系统管理 ===
    admin_management = "管理员管理"  # 超级管理员功能


class VersionTag(StrEnum):
    v1 = "v1"
