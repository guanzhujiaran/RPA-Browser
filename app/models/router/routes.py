"""
Router 模块 - 路由配置集中管理

提供所有路由的统一定义、分组导出和唯一性验证。
"""

from typing import Dict, List
from sqlmodel import SQLModel

from app.models.router.router_prefix import RouterPrefix
from app.models.router.tag import VersionTag, RouterTag


class RouterInfo(SQLModel):
    """路由信息模型"""

    version_tag: VersionTag
    router_tag: RouterTag
    router_prefix: RouterPrefix
    description: str = ""


# ====== 默认版本常量 ======
DEFAULT_VERSION = VersionTag.v1


# ====== 浏览器配置管理模块 (/browser) ======

browser_fingerprint_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.browser_fingerprint,
    router_prefix=RouterPrefix.BROWSER,
    description="浏览器指纹管理 - 提供指纹的生成、存储、查询、删除等功能",
)

browser_notification_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.browser_notification,
    router_prefix=RouterPrefix.BROWSER,
    description="通知配置管理 - 提供推送通知渠道的配置管理",
)

user_browser_default_settings_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.browser_default_settings,
    router_prefix=RouterPrefix.BROWSER,
    description="用户浏览器默认设置管理 - 提供用户级别浏览器默认设置的 CRUD 功能",
)


# ====== 浏览器运行时管理模块 ======

# 会话管理 (/browser/session)
browser_session_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.session_management,
    router_prefix=RouterPrefix.BROWSER_SESSION,
    description="浏览器会话管理 - 提供会话创建、心跳、状态查询等生命周期管理",
)

# 实时控制总入口 (/browser/control)
browser_control_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.browser_control,
    router_prefix=RouterPrefix.BROWSER_CONTROL,
    description="浏览器实时控制总入口",
)

# === browser_control 子模块路由 ===

# 自动化控制子路由
browser_control_operation_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.operation_control,
    router_prefix=RouterPrefix.BROWSER_CONTROL,
    description="自动化控制 - 暂停/恢复",
)

# 操作管理子路由
browser_control_execution_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.action_management,
    router_prefix=RouterPrefix.BROWSER_CONTROL,
    description="操作管理 - 操作执行、插件、工作流",
)


browser_control_webrtc_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.session_control,
    router_prefix=RouterPrefix.BROWSER_CONTROL,
    description="webrtc管理 - 连接视频流,直播显示浏览器画面",
)

browser_control_session_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.session_control,
    router_prefix=RouterPrefix.BROWSER_CONTROL,
    description="会话管理 - 健康检查、统计、清理",
)

# ====== 系统管理模块 ======

admin_router = RouterInfo(
    version_tag=DEFAULT_VERSION,
    router_tag=RouterTag.admin_management,
    router_prefix=RouterPrefix.ADMIN,
    description="管理员管理 - 提供全局会话查看、强制释放等超级管理员功能",
)


# ====== 路由分组导出（便于批量注册） ======

# 浏览器配置相关路由（/browser 前缀）
BROWSER_CONFIG_ROUTERS: List[RouterInfo] = [
    browser_fingerprint_router,
    browser_notification_router,
    user_browser_default_settings_router,
]

# 浏览器运行时相关路由
BROWSER_RUNTIME_ROUTERS: List[RouterInfo] = [
    browser_session_router,  # /browser/session
    browser_control_router,  # /browser/control
    # browser_control 子模块
    browser_control_operation_router,
    browser_control_execution_router,
    browser_control_security_router,
    browser_control_system_router,
]

# 系统管理相关路由
SYSTEM_ROUTERS: List[RouterInfo] = [
    admin_router,
]

# 所有路由（用于唯一性验证）
ALL_ROUTERS: List[RouterInfo] = [
    *BROWSER_CONFIG_ROUTERS,
    *BROWSER_RUNTIME_ROUTERS,
    *SYSTEM_ROUTERS,
]


# ====== 唯一性验证函数 ======


def validate_router_uniqueness() -> None:
    """验证所有路由的 (router_tag, router_prefix) 组合唯一性"""
    seen_keys: Dict[tuple, str] = {}

    for router in ALL_ROUTERS:
        key = (router.router_tag, router.router_prefix)
        if key in seen_keys:
            raise ValueError(
                f"路由配置冲突: router_tag={router.router_tag.value}, "
                f"router_prefix={router.router_prefix.value} "
                f"已被 {seen_keys[key]} 使用"
            )
        seen_keys[key] = router.router_tag.value


# 模块加载时自动执行验证
validate_router_uniqueness()


__all__ = [
    "RouterInfo",
    "DEFAULT_VERSION",
    "browser_fingerprint_router",
    "browser_notification_router",
    "user_browser_default_settings_router",
    "browser_session_router",
    "browser_control_router",
    "browser_control_operation_router",
    "browser_control_execution_router",
    "browser_control_webrtc_router",
    "browser_control_session_router",
    "admin_router",
    "BROWSER_CONFIG_ROUTERS",
    "BROWSER_RUNTIME_ROUTERS",
    "SYSTEM_ROUTERS",
    "ALL_ROUTERS",
    "validate_router_uniqueness",
]
