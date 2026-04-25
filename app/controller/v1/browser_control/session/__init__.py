"""会话管理模块"""

from fastapi import APIRouter
from app.controller.v1.browser_control.session.router import router as session_router

router = APIRouter()
router.include_router(session_router)
__all__ = ["router"]
