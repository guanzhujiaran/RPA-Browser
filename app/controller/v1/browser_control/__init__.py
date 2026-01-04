"""
浏览器实时控制路由模块

该模块包含了浏览器实时控制的所有路由，按业务类型拆分为以下子模块：
- session: 会话管理（心跳、会话创建、会话状态）
- operation: 操作控制（手动停止、操作状态、控制命令）
- plugin: 插件管理（插件暂停/恢复、插件状态）
- browser: 浏览器信息（浏览器信息、状态、导航、JS执行）
- stream: 视频流（视频流状态、截图）
- execution: 操作执行（点击、JS代码执行）
- system: 系统管理（系统统计、清理策略、系统清理、系统健康）
"""

from app.controller.v1.browser_control.base import new_router
from app.controller.v1.browser_control import (
    session,
    operation,
    plugin,
    browser,
    stream,
    execution,
    system,
)

# 创建主路由，聚合所有子路由
router = new_router()

# 包含所有子路由
router.include_router(session.router, tags=["会话管理"])
router.include_router(operation.router, tags=["操作控制"])
router.include_router(plugin.router, tags=["插件管理"])
router.include_router(browser.router, tags=["浏览器信息"])
router.include_router(stream.router, tags=["视频流"])
router.include_router(execution.router, tags=["操作执行"])
router.include_router(system.router, tags=["系统管理"])

__all__ = ["router"]
