"""浏览器配置管理路由模块

该模块包含浏览器静态配置相关的所有路由：
- fingerprint: 浏览器指纹管理
- plugin: 插件配置管理
- notification: 通知配置管理
- default_settings: 用户浏览器默认设置管理
"""
from fastapi import APIRouter
from app.controller.v1.browser.browser_router import router as fingerprint_router
from app.controller.v1.browser.plugin_router import router as plugin_router
from app.controller.v1.browser.notify_router import router as notify_router
from app.controller.v1.browser.default_settings_router import router as default_settings_router

router = APIRouter()
router.include_router(fingerprint_router)
router.include_router(plugin_router)
router.include_router(notify_router)
router.include_router(default_settings_router)

__all__ = ["router"]
