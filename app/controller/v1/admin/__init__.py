"""Admin controller package"""

from app.controller.v1.admin.admin_router import router as admin_sub_router
from app.controller.v1.admin.permission_router import router as permission_sub_router
from app.controller.v1.admin.report_router import router as report_sub_router
from app.controller.v1.admin.admin_role_router import router as admin_role_sub_router
from app.controller.v1.admin.approval_router import router as approval_sub_router
from app.controller.v1.admin.tag_router import router as tag_sub_router
from app.controller.v1.admin.certification_router import router as certification_sub_router
from app.controller.v1.admin.audit_router import router as audit_sub_router
from app.controller.v1.admin.user_ban_router import router as user_ban_sub_router
from app.models.router.all_routes import admin_router
from app.utils.controller.router_path import gen_api_router

router = gen_api_router(admin_router)
router.include_router(admin_sub_router)
router.include_router(permission_sub_router)
router.include_router(report_sub_router)
router.include_router(admin_role_sub_router)
router.include_router(approval_sub_router)
router.include_router(tag_sub_router)
router.include_router(certification_sub_router)
router.include_router(audit_sub_router)
router.include_router(user_ban_sub_router)

__all__ = ["router"]
