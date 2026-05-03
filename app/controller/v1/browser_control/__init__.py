"""浏览器实时控制路由模块

该模块包含了浏览器运行时管理的所有路由，按业务类型拆分为以下子模块：
- execution: 统一浏览器命令接口
- session: 会话管理（心跳、会话创建、状态查询）
- system: 系统管理（健康检查、统计、清理）
- pages: 页面管理（页面列表、切换、关闭）
"""

from fastapi import APIRouter
from app.controller.v1.browser_control.execution import router as execution_router
from app.controller.v1.browser_control.operation import router as operation_router
from app.controller.v1.browser_control.session import router as session_router
from app.controller.v1.browser_control.pages import router as pages_router
from app.controller.v1.browser_control.webrtc import router as webrtc_router

router = APIRouter()

# 子模块路由
router.include_router(execution_router)
router.include_router(operation_router)
router.include_router(session_router)
router.include_router(pages_router)
router.include_router(webrtc_router)

__all__ = ["router"]
