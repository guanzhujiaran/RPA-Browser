from enum import StrEnum


class RouterTag(StrEnum):
    # 浏览器指纹管理 - 提供浏览器指纹的创建、读取、更新、删除等功能
    browser_fingerprint = "浏览器指纹管理"

    # 浏览器会话管理 - 提供浏览器实例的会话创建、心跳、状态查询等功能
    browser_session = "浏览器会话管理"

    # 浏览器操作控制 - 提供浏览器实例的远程控制、点击、导航、截图等功能
    browser_operation = "浏览器操作控制"

    # 浏览器插件控制 - 提供浏览器实例插件的暂停、恢复、状态查询等功能
    browser_plugin_control = "浏览器插件控制"

    # 视频流管理 - 提供浏览器实例的视频流获取、WebRTC 连接等功能
    video_stream = "视频流管理"

    # 系统管理 - 提供系统统计、清理、健康检查、强制释放等功能
    system_management = "系统管理"

    # 安全检查 - 提供JavaScript代码安全检查和安全执行功能
    security_check = "安全检查"

    # 插件管理 - 提供浏览器插件的配置管理、安装、卸载等功能
    plugin_management = "插件管理"

    # 通知管理 - 提供推送通知的配置和管理功能
    notification_management = "通知管理"

    # 管理员管理 - 提供管理员查看所有会话、视频流、系统统计等功能
    admin_management = "管理员管理"


class VersionTag(StrEnum):
    v1 = "v1"