"""执行模块 - 分为四个独立的子模块"""

from fastapi import APIRouter
from app.controller.v1.browser_control.execution.action_router import (
    router as action_sub_router,
)
from app.controller.v1.browser_control.execution.workflow_router import (
    router as workflow_sub_router,
)
from app.controller.v1.browser_control.execution.plugin_router import (
    router as plugin_sub_router,
)
from app.controller.v1.browser_control.execution.community_router import (
    router as community_sub_router,
)

router = APIRouter()
router.include_router(action_sub_router)
router.include_router(workflow_sub_router)
router.include_router(plugin_sub_router)
router.include_router(community_sub_router)
__all__ = ["router"]
