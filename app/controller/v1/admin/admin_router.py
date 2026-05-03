"""管理员 API - 查看所有系统状态"""
from loguru import logger
from fastapi import APIRouter
import time
from app.config import settings
from app.models.response_code import ResponseCode
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_tag import RouterTag
from app.models.system.admin import (
    AdminAllSessionsResponse,
    BrowserSessionConfigResponse,
    UpdateBrowserSessionConfigRequest,
)
from app.services.RPA_browser.live_service import LiveService

router = APIRouter(tags=[RouterTag.admin_management])


@router.post("/sessions/all", response_model=StandardResponse[AdminAllSessionsResponse])
async def get_all_sessions():
    """获取所有浏览器会话信息（管理员）"""
    try:
        logger.info("👨‍💼 Admin: fetching all sessions")

        sessions = []
        for session_key, session_info in list(LiveService.browser_sessions.items()):
            session_data = {
                "mid": session_info.mid,
                "browser_id": session_info.browser_id,
                "session_key": session_key,
                "created_at": session_info.created_at,
                "last_activity": session_info.last_activity,
                "is_manual_mode": session_info.is_manual_mode,
                "priority": str(session_info.current_operation_priority),
                "active_connections": len(session_info.active_connections),
                "cleanup_policy": session_info.cleanup_policy,
            }
            sessions.append(session_data)

        response = AdminAllSessionsResponse(
            total=len(sessions),
            sessions=sessions,
        )

        return success_response(data=response)
    except Exception as e:
        logger.error(f"❌ Admin: failed to fetch sessions: {e}")
        return error_response(
            msg=f"Failed to fetch sessions: {str(e)}",
            code=ResponseCode.INTERNAL_ERROR,
        )


@router.get("/config/browser-session", response_model=StandardResponse[BrowserSessionConfigResponse])
async def get_browser_session_config():
    """获取浏览器会话配置（管理员）"""
    try:
        logger.info("👨‍💼 Admin: fetching browser session config")

        response = BrowserSessionConfigResponse(
            auto_cleanup=settings.browser_session_auto_cleanup,
            max_idle_time=settings.browser_session_max_idle_time,
            max_no_heartbeat_time=settings.browser_session_max_no_heartbeat_time,
            cleanup_interval=settings.browser_session_cleanup_interval,
            expiration_time=settings.browser_session_expiration_time,
        )

        return success_response(data=response)
    except Exception as e:
        logger.error(f"❌ Admin: failed to fetch browser session config: {e}")
        return error_response(
            msg=f"Failed to fetch browser session config: {str(e)}",
            code=ResponseCode.INTERNAL_ERROR,
        )


@router.post("/config/browser-session", response_model=StandardResponse[BrowserSessionConfigResponse])
async def update_browser_session_config(request: UpdateBrowserSessionConfigRequest):
    """更新浏览器会话配置（管理员）
    
    注意：此修改仅在内存中生效，重启服务后会恢复为环境变量中的配置。
    如需永久修改，请更新 .env 文件或环境变量。
    """
    try:
        logger.info("👨‍💼 Admin: updating browser session config")

        # 更新配置
        if request.auto_cleanup is not None:
            settings.browser_session_auto_cleanup = request.auto_cleanup
        if request.max_idle_time is not None:
            settings.browser_session_max_idle_time = request.max_idle_time
        if request.max_no_heartbeat_time is not None:
            settings.browser_session_max_no_heartbeat_time = request.max_no_heartbeat_time
        if request.cleanup_interval is not None:
            settings.browser_session_cleanup_interval = request.cleanup_interval
        if request.expiration_time is not None:
            settings.browser_session_expiration_time = request.expiration_time

        logger.info(
            f"✅ Browser session config updated: "
            f"auto_cleanup={settings.browser_session_auto_cleanup}, "
            f"max_idle_time={settings.browser_session_max_idle_time}s, "
            f"max_no_heartbeat_time={settings.browser_session_max_no_heartbeat_time}s, "
            f"cleanup_interval={settings.browser_session_cleanup_interval}s, "
            f"expiration_time={settings.browser_session_expiration_time}s"
        )

        response = BrowserSessionConfigResponse(
            auto_cleanup=settings.browser_session_auto_cleanup,
            max_idle_time=settings.browser_session_max_idle_time,
            max_no_heartbeat_time=settings.browser_session_max_no_heartbeat_time,
            cleanup_interval=settings.browser_session_cleanup_interval,
            expiration_time=settings.browser_session_expiration_time,
        )

        return success_response(data=response, msg="配置更新成功（仅内存中生效）")
    except Exception as e:
        logger.error(f"❌ Admin: failed to update browser session config: {e}")
        return error_response(
            msg=f"Failed to update browser session config: {str(e)}",
            code=ResponseCode.INTERNAL_ERROR,
        )
