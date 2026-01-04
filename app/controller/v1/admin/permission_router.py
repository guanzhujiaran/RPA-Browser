"""管理员权限配置 API"""
from loguru import logger
from fastapi import APIRouter

from app.config import settings
from app.models.response_code import ResponseCode
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_tag import RouterTag
from app.models.RPA_browser.permission_models import PermissionConfigList
from app.services.RPA_browser.permission_config_service import PermissionConfigService

router = APIRouter(prefix=settings.admin_base_path, tags=[RouterTag.admin_management])


@router.post("/permissions/get", response_model=StandardResponse[PermissionConfigList])
async def get_permissions():
    """获取权限配置（管理员）"""
    try:
        logger.info("👨‍💼 Admin: fetching permissions config")

        config = await PermissionConfigService.get_permissions()

        return success_response(data=config)
    except Exception as e:
        logger.error(f"❌ Admin: failed to fetch permissions: {e}")
        return error_response(
            msg=f"Failed to fetch permissions: {str(e)}",
            code=ResponseCode.INTERNAL_ERROR,
        )


@router.post("/permissions/update", response_model=StandardResponse[dict])
async def update_permissions(config: PermissionConfigList):
    """更新权限配置（管理员）"""
    try:
        logger.info("👨‍💼 Admin: updating permissions config")

        success = await PermissionConfigService.update_permissions(config)

        if success:
            return success_response(
                data={"message": "权限配置更新成功", "levels_count": len(config.levels)}
            )
        else:
            return error_response(
                msg="权限配置更新失败",
                code=ResponseCode.INTERNAL_ERROR,
            )
    except Exception as e:
        logger.error(f"❌ Admin: failed to update permissions: {e}")
        return error_response(
            msg=f"Failed to update permissions: {str(e)}",
            code=ResponseCode.INTERNAL_ERROR,
        )


@router.post("/permissions/reset", response_model=StandardResponse[dict])
async def reset_permissions():
    """重置权限配置为默认值（管理员）"""
    try:
        logger.info("👨‍💼 Admin: resetting permissions to default")

        success = await PermissionConfigService.reset_to_default()

        if success:
            return success_response(data={"message": "权限配置已重置为默认值"})
        else:
            return error_response(
                msg="权限配置重置失败",
                code=ResponseCode.INTERNAL_ERROR,
            )
    except Exception as e:
        logger.error(f"❌ Admin: failed to reset permissions: {e}")
        return error_response(
            msg=f"Failed to reset permissions: {str(e)}",
            code=ResponseCode.INTERNAL_ERROR,
        )
