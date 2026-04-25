"""
Router 模块 - 路由相关模型

定义路由前缀、标签和路由配置。
"""

from app.models.router.router_prefix import (
    RouterPrefix,
    BrowserFingerprintRouterPath,
    BrowserSessionRouterPath,
    BrowserControlRouterPath,
    PluginRouterPath,
    UserBrowserDefaultSettingRouterPath,
    NotifyRouterPath,
)
from app.models.router.tag import RouterTag, VersionTag, StrEnum
from app.models.router.routes import (
    RouterInfo,
    DEFAULT_VERSION,
    browser_fingerprint_router,
    browser_plugin_router,
    browser_notification_router,
    user_browser_default_settings_router,
    browser_session_router,
    browser_control_router,
    browser_control_operation_router,
    browser_control_execution_router,
    browser_control_security_router,
    browser_control_system_router,
    browser_control_webrtc_router,
    browser_control_session_router,
    system_router,
    admin_router,
    BROWSER_CONFIG_ROUTERS,
    BROWSER_RUNTIME_ROUTERS,
    SYSTEM_ROUTERS,
    ALL_ROUTERS,
    validate_router_uniqueness,
)

__all__ = [
    # Prefix
    "RouterPrefix",
    "BrowserFingerprintRouterPath",
    "BrowserSessionRouterPath",
    "BrowserControlRouterPath",
    "PluginRouterPath",
    "UserBrowserDefaultSettingRouterPath",
    "NotifyRouterPath",
    # Tag
    "RouterTag",
    "VersionTag",
    "StrEnum",
    # Routes
    "RouterInfo",
    "DEFAULT_VERSION",
    "browser_fingerprint_router",
    "browser_plugin_router",
    "browser_notification_router",
    "user_browser_default_settings_router",
    "browser_session_router",
    "browser_control_router",
    "browser_control_operation_router",
    "browser_control_execution_router",
    "browser_control_security_router",
    "browser_control_system_router",
    "browser_control_webrtc_router",
    "browser_control_session_router",
    "system_router",
    "admin_router",
    "BROWSER_CONFIG_ROUTERS",
    "BROWSER_RUNTIME_ROUTERS",
    "SYSTEM_ROUTERS",
    "ALL_ROUTERS",
    "validate_router_uniqueness",
]
