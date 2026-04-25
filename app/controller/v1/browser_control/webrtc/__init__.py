"""会话管理模块"""

from fastapi import APIRouter
from app.controller.v1.browser_control.webrtc.router import router as webrtc_router

router = APIRouter()
router.include_router(webrtc_router)
__all__ = ["router"]
