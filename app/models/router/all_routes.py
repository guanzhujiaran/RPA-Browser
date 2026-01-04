from sqlmodel import SQLModel

from app.models.router.router_prefix import RouterPrefix
from app.models.router.router_tag import VersionTag, RouterTag


class RouterInfo(SQLModel):
    version_tag: VersionTag
    router_tag: RouterTag
    router_prefix: RouterPrefix
    description: str = ""


# 浏览器指纹管理
browser_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.browser_fingerprint,
    router_prefix=RouterPrefix.browser,
    description="浏览器指纹管理API - 提供完整的浏览器指纹生命周期管理，包括生成随机指纹、创建、读取、更新、删除、统计和列表功能。支持用户代理、屏幕分辨率、时区、语言等浏览器特征的定制化配置。",
)

# 浏览器会话管理
browser_session_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.browser_session,
    router_prefix=RouterPrefix.browser_live_control,
    description="浏览器会话管理API - 提供浏览器实例的会话创建、心跳保持、状态查询、手动操作停止等功能。支持多客户端同时连接同一个浏览器实例，并提供会话生命周期管理。",
)

# 浏览器操作控制
browser_operation_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.browser_operation,
    router_prefix=RouterPrefix.browser_live_control,
    description="浏览器操作控制API - 提供浏览器实例的远程控制功能，包括执行控制命令、页面导航、JavaScript执行、截图、点击操作、获取浏览器信息和状态等。",
)

# 浏览器插件控制
browser_plugin_control_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.browser_plugin_control,
    router_prefix=RouterPrefix.browser_live_control,
    description="浏览器插件控制API - 提供浏览器实例插件的暂停、恢复、状态查询等功能。支持在手动操作模式下暂停自动插件，或在恢复后继续自动执行。",
)

# 视频流管理
video_stream_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.video_stream,
    router_prefix=RouterPrefix.browser_live_control,
    description="视频流管理API - 提供浏览器实例的视频流获取功能，包括MJPEG视频流和WebRTC实时流。支持视频流状态查询、WebRTC连接建立、ICE候选交换、连接状态管理和连接关闭。",
)

# 系统管理
system_management_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.system_management,
    router_prefix=RouterPrefix.browser_live_control,
    description="系统管理API - 提供系统级别的管理功能，包括系统统计信息获取、清理策略设置、系统清理触发、健康检查、强制释放浏览器实例等。",
)

# 安全检查
security_check_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.security_check,
    router_prefix=RouterPrefix.browser_live_control,
    description="安全检查API - 提供JavaScript代码的安全检查和安全执行功能。支持代码安全级别评估、危险操作检测、沙箱执行和超时控制。",
)

# 插件配置管理
plugin_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.plugin_management,
    router_prefix=RouterPrefix.browser,
    description="插件管理API - 提供浏览器插件的完整管理功能，包括插件的创建、更新、查询、删除等操作。支持多种插件类型的配置管理，如自动操作插件、监控插件等。",
)

# 通知管理
notify_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.notification_management,
    router_prefix=RouterPrefix.browser,
    description="通知管理API - 提供推送通知的配置和管理功能，支持多种通知渠道的配置。包括通知配置的创建、读取、更新、删除，以及针对特定浏览器实例的通知管理。",
)
