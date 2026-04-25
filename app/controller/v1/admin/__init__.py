"""Admin controller package"""

from app.controller.v1.admin.admin_router import router as admin_sub_router
from app.controller.v1.admin.permission_router import router as permission_sub_router
from app.models.router.all_routes import admin_router
from app.utils.controller.router_path import gen_api_router

router = gen_api_router(admin_router)
router.include_router(admin_sub_router)
router.include_router(permission_sub_router)

__all__ = ["router"]
