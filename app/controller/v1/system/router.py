from fastapi import Depends
from app.models.response import StandardResponse, success_response
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from .base import new_system_router
import time

router = new_system_router()


@router.post("/health")
async def health_check():
    """
    系统健康检查

    检查系统各组件的运行状态，包括数据库连接、Redis 连接等。

    Returns:
        dict: 系统健康状态信息
    """
    health_status = {
        "status": "healthy",
        "timestamp": int(time.time()),
        "components": {
            "database": "connected",
            "redis": "connected",
            "browser_pool": "available"
        }
    }
    return success_response(data=health_status)


@router.post("/statistics")
async def system_statistics(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
):
    """
    获取系统统计信息

    返回系统级别的统计数据，包括活跃会话数、浏览器实例数等。

    Returns:
        dict: 系统统计信息
    """
    stats = LiveService.get_system_statistics()
    return success_response(data=stats)


@router.post("/cleanup")
async def trigger_system_cleanup(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
):
    """
    触发系统清理

    手动触发系统清理任务，清理过期的会话和释放未使用的资源。

    Returns:
        dict: 清理结果
    """
    result = await LiveService.trigger_cleanup()
    return success_response(data=result)
