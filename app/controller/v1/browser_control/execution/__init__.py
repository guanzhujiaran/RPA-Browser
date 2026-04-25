"""执行模块"""

from fastapi import APIRouter
from app.controller.v1.browser_control.execution.execution_router import (
    router as action_router,
)

router = APIRouter()
router.include_router(action_router)
__all__ = ["router"]
