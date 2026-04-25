"""会话管理模块"""

from fastapi import APIRouter
from app.controller.v1.browser_control.operation.router import router as operation_router

router = APIRouter()
router.include_router(operation_router)
__all__ = ["router"]
