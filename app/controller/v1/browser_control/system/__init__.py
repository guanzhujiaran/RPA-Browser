"""系统管理模块"""

from fastapi import APIRouter
from app.controller.v1.browser_control.system.router import router as system_router

router = APIRouter()
router.include_router(system_router)
__all__ = ["router"]
