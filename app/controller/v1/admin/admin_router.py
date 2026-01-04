"""管理员 API - 查看所有系统状态"""
from loguru import logger
from fastapi import APIRouter

from app.config import settings
from app.models.response_code import ResponseCode
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_tag import RouterTag
from app.models.RPA_browser.admin_models import (
    AdminAllSessionsResponse,
    AdminAllStreamsResponse,
    AdminAllStatsResponse,
)
from app.services.RPA_browser.live_service import LiveService
from app.services.RPA_browser.webrtc_service import WebRTCService

router = APIRouter(prefix=settings.admin_base_path, tags=[RouterTag.admin_management])


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
                "cleanup_policy": {
                    "max_idle_time": session_info.cleanup_policy.max_idle_time,
                    "max_no_heartbeat_time": session_info.cleanup_policy.max_no_heartbeat_time,
                },
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


@router.post("/streams/all", response_model=StandardResponse[AdminAllStreamsResponse])
async def get_all_streams():
    """获取所有视频流信息（管理员）"""
    try:
        logger.info("👨‍💼 Admin: fetching all streams")

        live_streams = []
        for session_key, stream_info in list(LiveService.live_streams.items()):
            stream_data = {
                "session_key": session_key,
                "mid": stream_info.mid,
                "browser_id": stream_info.browser_id,
                "is_active": stream_info.is_active,
                "start_time": stream_info.start_time,
                "last_heartbeat": stream_info.last_heartbeat,
                "params": {
                    "fps": stream_info.params.fps if stream_info.params else None,
                    "crf": stream_info.params.crf if stream_info.params else None,
                    "preset": stream_info.params.preset if stream_info.params else None,
                    "gop": stream_info.params.gop if stream_info.params else None,
                    "tune": stream_info.params.tune if stream_info.params else None,
                    "video_bitrate": stream_info.params.video_bitrate if stream_info.params else None,
                    "audio_bitrate": stream_info.params.audio_bitrate if stream_info.params else None,
                },
            }
            live_streams.append(stream_data)

        webrtc_connections = []
        for conn_key, stream_info in list(WebRTCService.active_connections.items()):
            webrtc_data = {
                "connection_key": conn_key,
                "mid": stream_info.mid,
                "browser_id": stream_info.browser_id,
                "active": stream_info.active,
                "ice_connection_state": stream_info.peer_connection.iceConnectionState,
                "connection_state": stream_info.peer_connection.connectionState,
            }
            webrtc_connections.append(webrtc_data)

        response = AdminAllStreamsResponse(
            live_streams_count=len(live_streams),
            live_streams=live_streams,
            webrtc_connections_count=len(webrtc_connections),
            webrtc_connections=webrtc_connections,
        )

        return success_response(data=response)
    except Exception as e:
        logger.error(f"❌ Admin: failed to fetch streams: {e}")
        return error_response(
            msg=f"Failed to fetch streams: {str(e)}",
            code=ResponseCode.INTERNAL_ERROR,
        )


@router.post("/stats/all", response_model=StandardResponse[AdminAllStatsResponse])
async def get_all_stats():
    """获取所有系统统计信息（管理员）"""
    try:
        logger.info("👨‍💼 Admin: fetching all system stats")

        # 获取会话统计
        session_stats = LiveService.get_session_statistics()

        # 获取流统计
        live_streams_count = len(LiveService.live_streams)
        webrtc_connections_count = len(WebRTCService.active_connections)

        # 构建响应
        response = AdminAllStatsResponse(
            session_stats=session_stats.model_dump(),
            live_streams_count=live_streams_count,
            webrtc_connections_count=webrtc_connections_count,
            timestamp=int(__import__("time").time()),
        )

        return success_response(data=response)
    except Exception as e:
        logger.error(f"❌ Admin: failed to fetch stats: {e}")
        return error_response(
            msg=f"Failed to fetch stats: {str(e)}",
            code=ResponseCode.INTERNAL_ERROR,
        )
