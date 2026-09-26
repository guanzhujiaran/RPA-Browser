"""等级权限 / 浏览器指纹配额管理端接口（仅 root）。

配置落盘在 ``RPA-Browser/app/data/permissions.json``（容器内 ``/app/app/data/permissions.json``），
由 :class:`PermissionConfigService` 读写；本路由只暴露「查询 + 修改最大指纹数量」：

- ``permissions``（功能权限位）只读展示、保存时以磁盘现值回写，避免经配额接口变相提权；
- 每次请求直接读文件、无进程内缓存，保存后立即生效（详见文档 §5.19）。
"""

from bili_common.deps.auth import AuthInfo
from bili_common.models.response import (
    StandardResponse,
    error_response,
    success_response,
)
from bili_common.models.response_code import ResponseCode
from fastapi import APIRouter, Depends
from loguru import logger

from app.models.router.router_prefix import AdminPermissionRouterPath
from app.models.system.permission import (
    PermissionQuotaResp,
    PermissionQuotaUpdateReq,
)
from app.services.admin_audit import log_admin_action
from app.services.RPA_browser.permission_config_service import PermissionConfigService
from app.utils.depends.admin_depends import require_root

router = APIRouter()  # 由 admin/__init__.py 聚合父路由统一提供前缀 /api/admin/rpa


def _config_file_path() -> str:
    """实际读写的配置文件路径（展示给管理端，便于运维定位）。"""
    return str(PermissionConfigService.CONFIG_FILE)


@router.get(
    AdminPermissionRouterPath.permission_levels,
    response_model=StandardResponse[PermissionQuotaResp],
)
async def read_permission_quotas(
    auth: AuthInfo = Depends(require_root),
) -> StandardResponse:
    """查询各等级的最大浏览器指纹数量（仅 root）"""
    config = await PermissionConfigService.get_permissions()
    return success_response(
        data=PermissionQuotaResp(
            levels=config.levels, config_file=_config_file_path()
        )
    )


@router.post(
    AdminPermissionRouterPath.permission_update,
    response_model=StandardResponse[PermissionQuotaResp],
)
async def update_permission_quotas(
    request: PermissionQuotaUpdateReq,
    auth: AuthInfo = Depends(require_root),
) -> StandardResponse:
    """更新各等级的最大浏览器指纹数量（仅 root），写回 permissions.json 后立即生效"""
    try:
        config = await PermissionConfigService.update_max_fingerprints(request.levels)
    except ValueError as e:
        return error_response(code=ResponseCode.INVALID_PARAM, msg=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"❌ 更新等级指纹配额失败: {e}")
        return error_response(
            code=ResponseCode.INTERNAL_ERROR, msg=f"更新失败: {e}"
        )

    detail = ", ".join(f"{q.level_name}={q.max_fingerprints}" for q in request.levels)
    await log_admin_action(auth.mid, "permission:update", "permission", "levels", detail)
    return success_response(
        data=PermissionQuotaResp(
            levels=config.levels, config_file=_config_file_path()
        ),
        msg="等级指纹配额已保存，立即生效",
    )


@router.post(
    AdminPermissionRouterPath.permission_reset,
    response_model=StandardResponse[PermissionQuotaResp],
)
async def reset_permission_quotas(
    auth: AuthInfo = Depends(require_root),
) -> StandardResponse:
    """各等级指纹配额恢复为代码内默认值（仅 root），写回配置文件后立即生效"""
    try:
        await PermissionConfigService.reset_to_default()
        # 回读磁盘，返回的始终是「真实生效值」
        config = await PermissionConfigService.get_permissions()
    except Exception as e:  # noqa: BLE001
        logger.error(f"❌ 恢复等级指纹配额失败: {e}")
        return error_response(code=ResponseCode.INTERNAL_ERROR, msg=f"恢复失败: {e}")

    await log_admin_action(auth.mid, "permission:reset", "permission", "levels", "恢复默认配额")
    return success_response(
        data=PermissionQuotaResp(
            levels=config.levels, config_file=_config_file_path()
        ),
        msg="等级指纹配额已恢复默认，立即生效",
    )
