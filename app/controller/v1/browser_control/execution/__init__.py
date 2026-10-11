"""执行模块 - 分为独立的子模块

注意：插件（plugin）功能已整体下线（产品方案未定），plugin_router 已删除。
"""

from fastapi import APIRouter
from app.controller.v1.browser_control.execution.action_router import (
    router as action_sub_router,
)
from app.controller.v1.browser_control.execution.execution_router import (
    router as execution_sub_router,
)
from app.controller.v1.browser_control.execution.workflow_router import (
    router as workflow_sub_router,
)
from app.controller.v1.browser_control.execution.action_log_router import (
    router as action_log_sub_router,
)

router = APIRouter()
router.include_router(action_sub_router)
router.include_router(execution_sub_router)
router.include_router(workflow_sub_router)
router.include_router(action_log_sub_router)
__all__ = ["router"]
