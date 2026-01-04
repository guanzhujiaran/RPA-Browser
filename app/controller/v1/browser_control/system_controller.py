from fastapi import Depends
import time
from app.models.RPA_browser.live_control_models import (
    SystemStatisticsResponse,
    CleanupPolicyResponse,
    SystemCleanupResponse,
    SystemHealthCheckResponse,
    ForceReleaseResponse,
)
from app.models.RPA_browser.simplified_models import SimplifiedBrowserCleanupPolicyRequest
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.system_base import new_router

router = new_router()


@router.post(
    BrowserControlRouterPath.system_statistics,
    response_model=StandardResponse[SystemStatisticsResponse],
)
async def get_system_statistics():
    """
    获取系统统计信息

    获取整个系统的运行统计信息，包括会话数量、状态分布、连接数等。
    用于系统监控和性能分析。

    Returns:
        dict: 系统统计信息
    """
    statistics = LiveService.get_session_statistics()
    return success_response(
        data=SystemStatisticsResponse(
            total_sessions=statistics.total_sessions,
            active_sessions=statistics.status_distribution.get("active_sessions", 0),
            idle_sessions=statistics.status_distribution.get("idle_sessions", 0),
            total_active_connections=statistics.total_active_connections,
            manual_mode_sessions=statistics.manual_mode_sessions,
            video_streaming_sessions=0,  # SessionStatisticsData 没有这个字段
            uptime=0,  # SessionStatisticsData 没有这个字段
            timestamp=statistics.status_distribution.get("timestamp", int(time.time())),
        )
    )


@router.post(
    BrowserControlRouterPath.cleanup_policy,
    response_model=StandardResponse[CleanupPolicyResponse],
)
async def set_cleanup_policy(
    request: SimplifiedBrowserCleanupPolicyRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    设置清理策略

    为特定浏览器实例设置自定义的资源清理策略。
    可配置闲置时间、心跳超时等参数。

    Args:
        request: 包含browser_id和清理策略的请求

    Returns:
        dict: 设置结果
    """
    session_key = LiveService._get_session_key(auth_info.mid, browser_info.browser_id)

    if session_key not in LiveService.browser_sessions:
        return error_response(code=ResponseCode.NOT_FOUND, msg="浏览器会话不存在")

    entry = LiveService.browser_sessions[session_key]
    entry.cleanup_policy = request.policy

    return success_response(
        data=CleanupPolicyResponse(
            success=True,
            browser_id=browser_info.browser_id,
            message="清理策略设置成功",
            policy=request.policy,
        )
    )


@router.post(
    BrowserControlRouterPath.system_cleanup,
    response_model=StandardResponse[SystemCleanupResponse],
)
async def trigger_cleanup():
    """
    触发系统清理

    手动触发系统清理，立即清理过期的浏览器会话和闲置资源。
    用于紧急情况下的资源回收。

    Returns:
        dict: 清理结果统计
    """
    # 触发清理
    await LiveService.cleanup_expired_sessions()
    await LiveService._cleanup_idle_browsers()

    # 获取清理后的统计信息
    statistics = LiveService.get_session_statistics()
    statistics_response = SystemStatisticsResponse(
        total_sessions=statistics.total_sessions,
        active_sessions=statistics.status_distribution.get("active_sessions", 0),
        idle_sessions=statistics.status_distribution.get("idle_sessions", 0),
        total_active_connections=statistics.total_active_connections,
        manual_mode_sessions=statistics.manual_mode_sessions,
        video_streaming_sessions=0,  # SessionStatisticsData 没有这个字段
        uptime=0,  # SessionStatisticsData 没有这个字段
        timestamp=statistics.status_distribution.get("timestamp", int(time.time())),
    )

    return success_response(
        data=SystemCleanupResponse(
            success=True,
            message="系统清理完成",
            cleaned_sessions=0,  # SessionStatisticsData 没有这个字段
            cleaned_resources=0,  # SessionStatisticsData 没有这个字段
            statistics=statistics_response,
        )
    )


@router.post(
    BrowserControlRouterPath.system_health,
    response_model=StandardResponse[SystemHealthCheckResponse],
)
async def system_health_check():
    """
    系统健康检查

    检查系统各组件的运行状态，包括数据库连接、浏览器服务等。
    用于监控和故障诊断。

    Returns:
        dict: 系统健康状态信息
    """
    from app.utils.depends.session_manager import DatabaseSessionManager

    try:
        health_status = {
            "status": "healthy",
            "timestamp": int(time.time()),
            "checks": {},
        }

        # 数据库连接检查
        db_healthy = await DatabaseSessionManager.test_connection()
        health_status["checks"]["database"] = {
            "status": "healthy" if db_healthy else "unhealthy",
            "details": "连接正常" if db_healthy else "连接失败",
        }

        # 获取系统统计信息
        statistics = LiveService.get_session_statistics()
        health_status["checks"]["browser_service"] = {
            "status": "healthy",
            "details": {
                "total_sessions": statistics.total_sessions,
                "active_connections": statistics.total_active_connections,
                "manual_mode_sessions": statistics.manual_mode_sessions,
            },
        }

        # 综合判断系统状态
        if not db_healthy:
            health_status["status"] = "degraded"

        return success_response(
            data=SystemHealthCheckResponse(
                status=health_status["status"],
                timestamp=health_status["timestamp"],
                checks=health_status["checks"],
                uptime=health_status.get("uptime"),
            )
        )
    except Exception as e:
        # 健康检查失败
        return success_response(
            data=SystemHealthCheckResponse(
                status="unhealthy",
                timestamp=int(time.time()),
                error=str(e),
                checks={
                    "database": {"status": "unknown", "details": "检查失败"},
                    "browser_service": {"status": "unknown", "details": "检查失败"},
                },
            )
        )


@router.post(
    BrowserControlRouterPath.force_release,
    response_model=StandardResponse[ForceReleaseResponse],
)
async def force_release_browser(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    强制释放浏览器实例

    强制释放指定的浏览器实例，无论当前状态如何。
    将立即终止所有连接、关闭浏览器并清理相关资源。

    Args:
        request: 包含browser_id的请求

    Returns:
        dict: 释放结果

    Warning:
        此操作不可逆，将丢失所有未保存的状态和数据
    """
    result = await LiveService.release_browser_session(auth_info.mid, browser_info.browser_id)

    if result:
        return success_response(
            data=ForceReleaseResponse(
                success=True,
                browser_id=browser_info.browser_id,
                message=f"浏览器实例 {browser_info.browser_id} 已强制释放",
                released_at=int(time.time()),
            )
        )
