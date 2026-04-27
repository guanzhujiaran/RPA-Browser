"""
LiveService - 核心业务逻辑服务

此模块包含浏览器会话管理、心跳检测、人工操作干预等核心业务逻辑。
同时维护对 RPAOperationService 和 VideoStreamService 的引用以保持向后兼容。
"""

import time
import asyncio
import contextlib
from typing import Dict, Optional
from app.config import settings
from app.models.consts.enums import ConfigRunningModeEnum
from loguru import logger
from app.models.exceptions.base_exception import (
    BrowserNotStartedException,
    GetBrowserInfoFailedException,
)
from app.models.runtime.control import (
    BrowserStatusEnum,
    OperationPriority,
    BrowserStatus,
    HeartbeatRequest,
    HeartbeatResponse,
    ManualOperationRequest,
    AutomationResumeRequest,
    BrowserCleanupPolicy,
    SessionLifecycleState,
    CreateSessionRequest,
    BrowserInfoData,
    VideoStreamStatusData,
    ManualOperationResult,
    AutomationResult,
    OperationStatusData,
    PluginStatusData,
    SessionStatisticsData,
    CreateSessionData,
    BrowserSessionStatusData,
    VideoStreamParams,
)
from app.models.runtime.session import BrowserSessionRemoveParams
from app.models.runtime.operations import (
    RPAClickParams,
    RPAFillParams,
    RPAScrollParams,
    RPAScreenshotParams,
    RPAEvaluateParams,
    RPAWaitParams,
    RPANavigateParams,
    RPAResponse,
)
from app.models.runtime.live_service import (
    BrowserSessionEntry,
    LiveStreamingEntry,
)
from app.models.runtime.session import SessionCreateParams
from app.services.RPA_browser.browser_session_pool.playwright_pool import (
    get_default_session_pool,
)
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    PluginedSessionInfo,
)

# 导入分离的服务
from app.services.RPA_browser.rpa_operation_service import RPAOperationService
from app.services.RPA_browser.video_stream_service import VideoStreamService


class LiveService:
    """浏览器控制服务类 - 支持人工干预、心跳检测和自动清理"""

    # 维护浏览器会话状态
    browser_sessions: Dict[str, BrowserSessionEntry] = {}  # key: f"{mid}_{browser_id}"
    # 维护直播流状态
    live_streams: Dict[str, LiveStreamingEntry] = {}  # key: f"{mid}_{browser_id}"
    # 默认配置
    DEFAULT_SESSION_TIMEOUT = 3600  # 1小时
    DEFAULT_HEARTBEAT_INTERVAL = 30  # 心跳间隔30秒
    DEFAULT_CLEANUP_INTERVAL = 300  # 清理间隔5分钟
    DEFAULT_LIVE_STREAM_TIMEOUT = 60  # 直播流超时时间60秒

    # 向后兼容：引用分离的服务
    RPAOperationService = RPAOperationService

    @staticmethod
    def _get_session_key(mid: int, browser_id: int) -> str:
        """获取会话键"""
        return f"{mid}_{browser_id}"

    @staticmethod
    def _parse_session_key(session_key: str) -> tuple[int, int]:
        """解析会话键，返回 (mid, browser_id)"""
        try:
            parts = session_key.rsplit("_", 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid session key format: {session_key}")
            mid = int(parts[0])
            browser_id = int(parts[1])
            return mid, browser_id
        except ValueError as e:
            logger.error(f"解析会话键失败: {session_key}, error: {e}")
            raise

    @staticmethod
    async def _check_heartbeat_timeouts():
        """检查心跳超时 - 使用状态机判断会话清理"""
        current_time = int(time.time())
        sessions_to_cleanup = []

        for session_key, entry in list(LiveService.browser_sessions.items()):
            # 使用状态机评估会话状态
            cleanup_decision = LiveService._evaluate_session_cleanup(
                entry, current_time
            )
            
            if cleanup_decision.should_cleanup:
                logger.warning(
                    f"会话 {session_key} 需要清理 - 原因: {cleanup_decision.reason}, "
                    f"状态: {entry.lifecycle_state.value} -> {cleanup_decision.next_state.value}"
                )
                sessions_to_cleanup.append((session_key, cleanup_decision))
            elif cleanup_decision.next_state != entry.lifecycle_state:
                # 状态转换但不需要清理
                old_state = entry.lifecycle_state
                entry.lifecycle_state = cleanup_decision.next_state
                logger.debug(
                    f"会话 {session_key} 状态转换: {old_state.value} -> {entry.lifecycle_state.value}"
                )

        # 执行清理
        for session_key, decision in sessions_to_cleanup:
            try:
                mid, browser_id = LiveService._parse_session_key(session_key)
                entry = LiveService.browser_sessions[session_key]
                
                # 更新为终止中状态
                entry.lifecycle_state = SessionLifecycleState.TERMINATING
                
                # 根据清理原因执行不同的清理策略
                await LiveService._execute_cleanup_strategy(entry, decision)
                
                # 释放浏览器会话
                await LiveService.release_browser_session(mid, browser_id)
                logger.info(f"已清理会话: {session_key}, 原因: {decision.reason}")
            except Exception as e:
                logger.error(f"清理会话失败: {session_key}, error: {e}")

    @staticmethod
    def _evaluate_session_cleanup(
        entry: "BrowserSessionEntry", current_time: int
    ) -> "CleanupDecision":
        """
        评估会话是否需要清理 - 状态机核心逻辑
        
        优先级顺序（从高到低）:
        1. 过期时间检查 (expires_at)
        2. 心跳超时检查 (heartbeat timeout)
        3. 闲置时间检查 (idle timeout)
        4. 直播流超时检查 (live stream timeout)
        
        Returns:
            CleanupDecision: 清理决策，包含是否清理、原因、下一个状态
        """
        from dataclasses import dataclass
        
        @dataclass
        class CleanupDecision:
            should_cleanup: bool = False
            reason: str = ""
            next_state: SessionLifecycleState = SessionLifecycleState.ACTIVE
            priority: int = 0  # 优先级，数字越小优先级越高
        
        policy = entry.cleanup_policy
        decision = CleanupDecision()
        
        # === 优先级 1: 检查是否已过期 (expires_at) ===
        if entry.expires_at and current_time > entry.expires_at:
            return CleanupDecision(
                should_cleanup=True,
                reason="会话已过期",
                next_state=SessionLifecycleState.TERMINATING,
                priority=1
            )
        
        # === 优先级 2: 检查心跳超时 ===
        time_since_last_heartbeat = current_time - entry.last_heartbeat
        has_active_clients = len(entry.heartbeat_clients) > 0
        
        # 如果有活跃客户端，不会因心跳超时而清理
        if not has_active_clients and time_since_last_heartbeat > policy.max_no_heartbeat_time:
            # 如果处于人工操作模式，先恢复自动化
            if entry.is_manual_mode:
                return CleanupDecision(
                    should_cleanup=False,
                    reason="无活跃心跳，需要恢复自动化",
                    next_state=SessionLifecycleState.ACTIVE,
                    priority=2
                )
            
            # 心跳超时，需要清理
            return CleanupDecision(
                should_cleanup=True,
                reason=f"心跳超时 ({time_since_last_heartbeat}s > {policy.max_no_heartbeat_time}s)",
                next_state=SessionLifecycleState.TERMINATING,
                priority=2
            )
        
        # === 优先级 3: 检查闲置超时 ===
        time_since_last_activity = current_time - entry.last_activity
        is_idle = entry.status == BrowserStatusEnum.IDLE
        no_active_connections = len(entry.active_connections) == 0
        
        if is_idle and no_active_connections and time_since_last_activity > policy.max_idle_time:
            return CleanupDecision(
                should_cleanup=True,
                reason=f"闲置超时 ({time_since_last_activity}s > {policy.max_idle_time}s)",
                next_state=SessionLifecycleState.TERMINATING,
                priority=3
            )
        
        # === 状态转换逻辑（不清理，只更新状态）===
        
        # 从 IDLE 恢复到 ACTIVE
        if entry.lifecycle_state == SessionLifecycleState.IDLE and (has_active_clients or entry.status == BrowserStatusEnum.RUNNING):
            return CleanupDecision(
                should_cleanup=False,
                reason="从闲置恢复活跃",
                next_state=SessionLifecycleState.ACTIVE,
                priority=99
            )
        
        # 从 ACTIVE 转为 IDLE
        if entry.lifecycle_state == SessionLifecycleState.ACTIVE and is_idle and no_active_connections:
            return CleanupDecision(
                should_cleanup=False,
                reason="进入闲置状态",
                next_state=SessionLifecycleState.IDLE,
                priority=99
            )
        
        # 默认保持当前状态
        return CleanupDecision(
            should_cleanup=False,
            reason="状态正常",
            next_state=entry.lifecycle_state,
            priority=99
        )
    
    @staticmethod
    async def _execute_cleanup_strategy(
        entry: "BrowserSessionEntry", 
        decision: "CleanupDecision"
    ):
        """
        执行清理策略 - 根据不同的清理原因执行不同的清理动作
        """
        # 如果是心跳超时导致的清理，先尝试恢复自动化
        if "心跳超时" in decision.reason and entry.is_manual_mode:
            logger.info(f"清理前恢复自动化: {entry.mid}_{entry.browser_id}")
            try:
                await LiveService.resume_automation(entry.mid, entry.browser_id)
            except Exception as e:
                logger.warning(f"恢复自动化失败（继续清理）: {e}")
        
        # 清理相关的直播流
        session_key = LiveService._get_session_key(entry.mid, entry.browser_id)
        if session_key in LiveService.live_streams:
            logger.info(f"清理关联的直播流: {session_key}")
            await LiveService._cleanup_live_stream(entry.mid, entry.browser_id)

    @staticmethod
    async def _check_live_stream_timeouts():
        """检查直播流超时 - 整合到状态机中"""
        current_time = int(time.time())
        streams_to_cleanup = []

        for stream_key, entry in list(LiveService.live_streams.items()):
            # 检查直播流是否超时
            time_since_last_heartbeat = current_time - entry.last_heartbeat
            if time_since_last_heartbeat > LiveService.DEFAULT_LIVE_STREAM_TIMEOUT:
                logger.warning(
                    f"直播流超时: {stream_key}, "
                    f"最后心跳: {entry.last_heartbeat}, "
                    f"超时时长: {time_since_last_heartbeat}s"
                )
                # 标记为超时
                entry.is_active = False
                streams_to_cleanup.append(stream_key)

        # 清理超时直播流
        for stream_key in streams_to_cleanup:
            try:
                mid, browser_id = LiveService._parse_session_key(stream_key)
                await LiveService._cleanup_live_stream(mid, browser_id)
                logger.info(f"已清理超时直播流: {stream_key}")
            except Exception as e:
                logger.error(f"清理直播流失败: {stream_key}, error: {e}")

    @staticmethod
    async def start_live_streaming(
        mid: int, browser_id: int, params: Optional[VideoStreamParams]=None
    ):
        """开始直播流管理"""
        stream_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # 如果已有直播流，先停止其他任务
        if stream_key in LiveService.live_streams and stream_key in VideoStreamService.video_streams:
            await VideoStreamService.stop_video_stream(mid, browser_id, force=True)

        # 创建或更新直播流条目
        LiveService.live_streams[stream_key] = LiveStreamingEntry(
            mid=mid,
            browser_id=browser_id,
            start_time=current_time,
            last_heartbeat=current_time,
            is_active=True,
            stream_params=params,
        )

        # 更新会话状态为直播中
        if stream_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[stream_key]
            entry.status = BrowserStatusEnum.RUNNING
            entry.last_activity = current_time

        logger.info(f"开始直播流管理: {stream_key}")

    @staticmethod
    async def _cleanup_live_stream(mid: int, browser_id: int):
        """清理直播流"""
        stream_key = LiveService._get_session_key(mid, browser_id)

        # 停止视频流
        if stream_key in VideoStreamService.video_streams:
            await VideoStreamService.stop_video_stream(mid, browser_id, force=True)

        # 从直播流管理中移除
        if stream_key in LiveService.live_streams:
            del LiveService.live_streams[stream_key]

        # 恢复自动化（如果有会话且无其他活跃连接）
        if stream_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[stream_key]

            # 检查是否还有其他活跃连接
            if not entry.active_connections and not entry.heartbeat_clients:
                # 恢复自动化
                await LiveService.resume_automation(mid, browser_id)
                # 如果是因为心跳超时导致的清理或会话处于闲置状态，直接释放浏览器资源
                current_time = int(time.time())
                if (current_time - entry.last_heartbeat > LiveService.DEFAULT_LIVE_STREAM_TIMEOUT or entry.status == BrowserStatusEnum.IDLE):
                    logger.warning(f"直播流心跳超时或会话闲置，释放浏览器资源: {stream_key}")
                    await LiveService.release_browser_session(mid, browser_id)

        logger.info(f"清理直播流完成: {stream_key}")

    @staticmethod
    async def handle_heartbeat(
        mid: int, browser_id: int, heartbeat: HeartbeatRequest
    ) -> HeartbeatResponse:
        """处理心跳请求"""
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        if session_key not in LiveService.browser_sessions:
            return HeartbeatResponse(
                success=False,
                server_timestamp=current_time,
                next_heartbeat_interval=LiveService.DEFAULT_HEARTBEAT_INTERVAL,
                status="session_not_found",
            )

        entry = LiveService.browser_sessions[session_key]

        # 更新直播流心跳（如果存在）
        if session_key in LiveService.live_streams:
            live_entry = LiveService.live_streams[session_key]
            live_entry.last_heartbeat = current_time
            live_entry.is_active = True

            # 如果直播流曾被标记为清理，重新激活
            if live_entry.cleanup_scheduled:
                live_entry.cleanup_scheduled = False
                logger.info(f"直播流重新激活: {session_key}")

        # 更新心跳时间
        entry.last_heartbeat = current_time
        entry.heartbeat_clients[heartbeat.client_id] = current_time
        entry.last_activity = current_time

        # 更新状态
        if not entry.active_connections:
            entry.active_connections.add(heartbeat.client_id)

        # 检查是否需要自动启动直播流
        if session_key not in LiveService.live_streams and not entry.heartbeat_clients:
            # 首次心跳，自动启动直播流管理
            await LiveService.start_live_streaming(mid, browser_id)

        return HeartbeatResponse(
            success=True,
            server_timestamp=current_time,
            next_heartbeat_interval=LiveService.DEFAULT_HEARTBEAT_INTERVAL,
            status="heartbeat_received",
            active_clients=len(entry.active_connections),
        )

    @staticmethod
    async def start_manual_operation(
        mid: int, browser_id: int, request: ManualOperationRequest
    ) -> ManualOperationResult:
        """开始人工操作"""
        session_key = LiveService._get_session_key(mid, browser_id)

        if session_key not in LiveService.browser_sessions:
            # 创建默认会话请求
            default_request = CreateSessionRequest(
                headless=False,
                auto_cleanup=True,
            )
            await LiveService.create_browser_session(mid, browser_id, default_request)

        entry = LiveService.browser_sessions[session_key]
        current_time = int(time.time())

        # 如果当前有更高或相同优先级的操作在进行，返回冲突
        if (
            entry.is_manual_mode
            and request.priority.value <= entry.current_operation_priority.value
        ):
            return ManualOperationResult(
                success=False,
                message=f"当前已有更高或相同优先级的操作在进行: {entry.current_operation_priority.value}",
                status="conflict",
                priority=entry.current_operation_priority.value,
                start_time=0,
            )
        
        # 暂停自动化任务
        await LiveService.pause_plugins(mid, browser_id)

        # 更新状态
        entry.is_manual_mode = True
        entry.current_operation_priority = request.priority
        entry.manual_operation_start_time = current_time
        entry.status = BrowserStatusEnum.PAUSED

        message = f"人工操作已开始，优先级: {request.priority.value}"
        if request.reason:
            message += f", 原因: {request.reason}"

        return ManualOperationResult(
            success=True,
            message=message,
            status="manual_mode_active",
            priority=request.priority.value,
            start_time=current_time,
        )

    @staticmethod
    async def resume_automation(
        mid: int, browser_id: int, request: Optional[AutomationResumeRequest]=None
    ) -> AutomationResult:
        """恢复自动化任务"""
        session_key = LiveService._get_session_key(mid, browser_id)

        if session_key not in LiveService.browser_sessions:
            return AutomationResult(
                success=False, message="会话不存在", status="error", resume_time=0
            )

        entry = LiveService.browser_sessions[session_key]

        if not entry.is_manual_mode:
            return AutomationResult(
                success=False,
                message="当前未处于人工操作模式",
                status="not_manual_mode",
                resume_time=0,
            )

        # 恢复插件自动操作
        try:
            await LiveService.resume_plugins(mid, browser_id)

            # 重置状态
            entry.is_manual_mode = False
            entry.current_operation_priority = OperationPriority.NORMAL
            entry.status = BrowserStatusEnum.RUNNING
            entry.automation_paused_time = 0

            message = "自动化任务已恢复"
            if request and request.reason:
                message += f", 原因: {request.reason}"

            return AutomationResult(
                success=True,
                message=message,
                status="automation_resumed",
                resume_time=int(time.time()),
            )

        except Exception as e:
            return AutomationResult(
                success=False,
                message=f"恢复自动化失败: {str(e)}",
                status="error",
                resume_time=0,
            )

    @staticmethod
    def get_operation_status(mid: int, browser_id: int) -> OperationStatusData:
        """获取操作状态"""
        session_key = LiveService._get_session_key(mid, browser_id)

        if session_key not in LiveService.browser_sessions:
            return OperationStatusData(
                status="not_found",
                is_manual_mode=False,
                current_priority="none",
                active_connections=0,
                last_activity=0,
                last_heartbeat=0,
                manual_operation_duration=0,
                heartbeat_clients=[],
            )

        entry = LiveService.browser_sessions[session_key]
        current_time = int(time.time())

        return OperationStatusData(
            status=entry.status.value,
            is_manual_mode=entry.is_manual_mode,
            current_priority=entry.current_operation_priority.value,
            active_connections=len(entry.active_connections),
            last_activity=entry.last_activity,
            last_heartbeat=entry.last_heartbeat,
            manual_operation_duration=(
                current_time - entry.manual_operation_start_time
                if entry.is_manual_mode
                else 0
            ),
            heartbeat_clients=list(entry.heartbeat_clients.keys()),
        )

    @staticmethod
    async def get_or_create_browser_session(
        mid: int,
        browser_id: int,
        headless: bool=False,
        is_create_browser: bool=True,
    ) -> PluginedSessionInfo:
        """获取插件化浏览器会话"""
        pool = get_default_session_pool()
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # 检查是否已有会话
        if session_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[session_key]
            entry.last_activity = current_time
            return entry.plugined_session
        
        if not is_create_browser:
            raise BrowserNotStartedException()
        
        # 获取浏览器会话
        session_params = SessionCreateParams(
            mid=mid,
            browser_id=browser_id,
            headless=headless,
        )
        plugined_session = await pool.get_session(session_params)

        # 创建会话条目
        entry = BrowserSessionEntry(
            mid=mid,
            browser_id=browser_id,
            plugined_session=plugined_session,
            last_activity=current_time,
            last_heartbeat=current_time,
        )

        LiveService.browser_sessions[session_key] = entry

        return plugined_session

    @staticmethod
    async def execute_browser_command(
        mid: int, browser_id: int, command
    ) -> RPAResponse:
        """执行浏览器命令 - 支持优先级和人工操作检测

        此方法通过统一的命令接口执行各种浏览器操作。

        Args:
            mid: 用户ID
            browser_id: 浏览器ID
            command: LiveControlCommand 对象或字典

        Returns:
            RPAResponse: 操作结果
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return RPAResponse(success=False, error="会话不存在")

        # 处理字典类型的命令
        if isinstance(command, dict):
            command_type = command.get("type")
            params = command.get("params", {})
            require_manual_mode = command.get("require_manual_mode", False)
            priority = command.get("priority", OperationPriority.NORMAL)
            interrupt_automation = command.get("interrupt_automation", True)
        else:
            # 处理 LiveControlCommand 对象
            command_type = command.type
            params = command.params
            require_manual_mode = command.require_manual_mode
            priority = command.priority
            interrupt_automation = command.interrupt_automation

        # 检查是否需要人工操作模式
        if require_manual_mode and not entry.is_manual_mode:
            return RPAResponse(success=False, error="该命令需要人工操作模式")

        # 检查当前操作优先级
        if (
            entry.is_manual_mode
            and priority.value <= entry.current_operation_priority.value
        ):
            return RPAResponse(
                success=False,
                error=f"当前人工操作优先级({entry.current_operation_priority.value})更高，无法执行此命令",
            )

        # 如果命令需要中断自动化且当前处于自动化模式，则暂停自动化
        if interrupt_automation and not entry.is_manual_mode:
            await LiveService.pause_plugins(mid, browser_id)
            entry.is_manual_mode = True
            entry.status = BrowserStatusEnum.PAUSED
            entry.manual_operation_start_time = int(time.time())

        try:
            page = await entry.plugined_session.get_current_page()

            # 更新活动时间和状态
            entry.last_activity = int(time.time())

            # 根据命令类型执行相应的RPA操作
            if command_type == "click":
                return await RPAOperationService.click_element(
                    page, RPAClickParams(**params)
                )
            elif command_type == "fill":
                return await RPAOperationService.fill_form(
                    page, RPAFillParams(**params)
                )
            elif command_type == "scroll":
                return await RPAOperationService.scroll_page(
                    page, RPAScrollParams(**params)
                )
            elif command_type == "screenshot":
                return await RPAOperationService.take_screenshot(
                    page, RPAScreenshotParams(**params)
                )
            elif command_type == "evaluate":
                return await RPAOperationService.evaluate_script(
                    page, RPAEvaluateParams(**params)
                )
            elif command_type == "wait":
                return await RPAOperationService.wait_for_element(
                    page, RPAWaitParams(**params)
                )
            elif command_type == "navigate":
                return await RPAOperationService.navigate_to(
                    page, RPANavigateParams(**params)
                )
            elif command_type == "get_browser_info":
                # 获取完整的浏览器信息
                browser_info = await RPAOperationService.get_browser_info(
                    entry.plugined_session
                )
                return RPAResponse(success=True, data=browser_info)
            else:
                return RPAResponse(success=False, error=f"未知命令类型: {command_type}")

        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def get_browser_info(mid: int, browser_id: int) -> BrowserInfoData:
        """获取浏览器信息

        统一接口：通过 execute_browser_command 调用

        Args:
            mid: 用户ID
            browser_id: 浏览器ID

        Returns:
            BrowserInfoData: 浏览器信息数据
        """
        # 构建命令
        command = {
            "type": "get_browser_info",
            "params": {},
            "require_manual_mode": False,
            "interrupt_automation": False,
        }
        result = await LiveService.execute_browser_command(mid, browser_id, command)
        if not result.success:
            raise GetBrowserInfoFailedException(result.error or "未知错误")
        return result.data

    @staticmethod
    async def pause_plugins(mid: int, browser_id: int) -> PluginStatusData:
        """暂停插件自动操作"""
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return PluginStatusData(is_paused=False, message="会话不存在")

        try:
            entry.plugined_session.pause_plugins()
            return PluginStatusData(
                is_paused=True, message="插件自动操作已暂停，启用手动操作模式"
            )
        except Exception as e:
            return PluginStatusData(is_paused=False, message=f"暂停插件失败: {str(e)}")

    @staticmethod
    async def resume_plugins(mid: int, browser_id: int) -> PluginStatusData:
        """恢复插件自动操作"""
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return PluginStatusData(is_paused=True, message="会话不存在")

        try:
            entry.plugined_session.resume_plugins()
            return PluginStatusData(is_paused=False, message="插件自动操作已恢复")
        except Exception as e:
            return PluginStatusData(is_paused=True, message=f"恢复插件失败: {str(e)}")

    @staticmethod
    def get_plugin_status(mid: int, browser_id: int) -> PluginStatusData:
        """获取插件状态"""
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return PluginStatusData(is_paused=False, message="会话不存在")

        try:
            is_paused = entry.plugined_session.is_plugins_paused()
            return PluginStatusData(
                is_paused=is_paused,
                message="插件已暂停" if is_paused else "插件正常运行",
            )
        except Exception as e:
            return PluginStatusData(
                is_paused=False, message=f"获取插件状态失败: {str(e)}"
            )

    @staticmethod
    async def release_browser_session(mid: int, browser_id: int) -> bool:
        """释放浏览器会话"""
        session_key = LiveService._get_session_key(mid, browser_id)

        try:
            pool = get_default_session_pool()

            # 清理 WebRTC 连接
            from app.services.RPA_browser.webrtc_service import WebRTCService

            connection_key = WebRTCService._get_connection_key(mid, browser_id)
            if connection_key in WebRTCService.active_connections:
                logger.info(f"清理 WebRTC 连接: {connection_key}")
                await WebRTCService.close_connection(mid, browser_id)

            # 关闭浏览器会话
            if session_key in LiveService.browser_sessions:
                entry = LiveService.browser_sessions[session_key]
                with contextlib.suppress(Exception):
                    # 恢复插件状态
                    if entry.is_manual_mode:
                        await entry.plugined_session.resume_plugins()
                    await entry.plugined_session.close()
                del LiveService.browser_sessions[session_key]

            # 从池中释放会话
            remove_params = BrowserSessionRemoveParams(
                mid=mid,
                browser_id=browser_id,
                force_close=False,
            )

            await pool.release_session(remove_params)

            # 清理相关视频流
            if session_key in VideoStreamService.video_streams:
                await VideoStreamService.stop_video_stream(mid, browser_id, force=True)

            return True

        except Exception as e:
            logger.error(
                f"释放浏览器会话失败 (mid={mid}, browser_id={browser_id}): {e}"
            )
            return False

    @staticmethod
    def get_browser_status(mid: int, browser_id: int) -> Optional[BrowserStatus]:
        """获取浏览器状态"""
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return None

        return BrowserStatus(
            mid=mid,
            browser_id=browser_id,
            status=entry.status,
            active_connections=len(entry.active_connections),
            last_activity=entry.last_activity,
            last_heartbeat=entry.last_heartbeat,
            is_manual_mode=entry.is_manual_mode,
            current_operation_priority=entry.current_operation_priority,
        )

    @staticmethod
    def get_session_statistics() -> SessionStatisticsData:
        """获取会话统计信息"""
        total_sessions = len(LiveService.browser_sessions)
        running_sessions = len([
            entry for entry in LiveService.browser_sessions.values()
            if entry.status == BrowserStatusEnum.RUNNING
        ])
        paused_sessions = len([
            entry for entry in LiveService.browser_sessions.values()
            if entry.status == BrowserStatusEnum.PAUSED
        ])
        idle_sessions = len([
            entry for entry in LiveService.browser_sessions.values()
            if entry.status == BrowserStatusEnum.IDLE
        ])
        manual_mode_sessions = len([
            entry for entry in LiveService.browser_sessions.values() if entry.is_manual_mode
        ])

        total_connections = sum(
            len(entry.active_connections)
            for entry in LiveService.browser_sessions.values()
        )
        total_heartbeat_clients = sum(
            len(entry.heartbeat_clients)
            for entry in LiveService.browser_sessions.values()
        )

        return SessionStatisticsData(
            total_sessions=total_sessions,
            status_distribution={
                "running": running_sessions,
                "paused": paused_sessions,
                "idle": idle_sessions,
                "stopped": 0,
                "error": 0,
            },
            manual_mode_sessions=manual_mode_sessions,
            total_active_connections=total_connections,
            total_heartbeat_clients=total_heartbeat_clients,
            session_timeout=LiveService.DEFAULT_SESSION_TIMEOUT,
            heartbeat_interval=LiveService.DEFAULT_HEARTBEAT_INTERVAL,
            cleanup_interval=LiveService.DEFAULT_CLEANUP_INTERVAL,
        )

    @staticmethod
    async def create_browser_session(
        mid: int, browser_id: int, request: CreateSessionRequest
    ) -> CreateSessionData:
        """
        创建浏览器会话

        这是一个独立的会话创建接口，与心跳机制完全解耦。
        只有显式调用此接口才会创建浏览器会话。
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # 检查会话是否已存在
        if session_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[session_key]

            # 确保向后兼容性
            created_at = getattr(entry, "created_at", entry.last_activity)
            expires_at = getattr(entry, "expires_at", None)

            return CreateSessionData(
                success=True,
                session_id=session_key,
                browser_started=True,
                created_at=created_at,
                expires_at=expires_at,
                message="会话已存在，返回现有会话信息",
            )

        try:
            # 创建新的浏览器会话
            await LiveService.get_or_create_browser_session(
                mid, browser_id
            )

            # 获取会话条目并设置生命周期状态
            entry = LiveService.browser_sessions[session_key]
            entry.lifecycle_state = SessionLifecycleState.ACTIVE
            entry.expires_at = (
                current_time + request.expiration_time
                if request.expiration_time
                else None
            )

            # 设置清理策略
            if request.cleanup_policy:
                entry.cleanup_policy = request.cleanup_policy
            elif request.auto_cleanup:
                entry.cleanup_policy = BrowserCleanupPolicy(
                    max_idle_time=1800, max_no_heartbeat_time=300, cleanup_interval=300
                )

            return CreateSessionData(
                success=True,
                session_id=session_key,
                browser_started=True,
                created_at=entry.created_at,
                expires_at=entry.expires_at,
                message="浏览器会话创建成功",
            )

        except Exception as e:
            return CreateSessionData(
                success=False,
                session_id=session_key,
                browser_started=False,
                created_at=0,
                expires_at=None,
                error=f"创建会话失败: {str(e)}",
            )

    @staticmethod
    async def create_browser_session_background(
        mid: int, browser_id: int, request: CreateSessionRequest
    ) -> None:
        """
        后台创建浏览器会话

        这个方法在后台任务中执行，不返回结果给客户端。
        包含重试机制，最多重试 3 次。
        """
        max_retries = 3
        retry_delay = 5  # 秒
        start_time = time.time()
        for attempt in range(max_retries):
            try:
                # 检查会话是否已存在
                session_key = LiveService._get_session_key(mid, browser_id)
                if session_key in LiveService.browser_sessions:
                    logger.debug(f"会话已存在，跳过创建: {session_key}")
                    return

                # 创建新的浏览器会话
                await LiveService.get_or_create_browser_session(
                    mid, browser_id
                )

                # 获取会话条目并设置生命周期状态
                current_time = int(time.time())
                entry = LiveService.browser_sessions[session_key]
                entry.lifecycle_state = SessionLifecycleState.ACTIVE
                entry.expires_at = (
                    current_time + request.expiration_time
                    if request.expiration_time
                    else None
                )

                # 设置清理策略
                if request.cleanup_policy:
                    entry.cleanup_policy = request.cleanup_policy
                elif request.auto_cleanup:
                    entry.cleanup_policy = BrowserCleanupPolicy(
                        max_idle_time=1800,
                        max_no_heartbeat_time=300,
                        cleanup_interval=300,
                    )

                logger.info(f"后台创建浏览器会话成功: {session_key}\n耗时:{time.time() - start_time}秒")
                return

            except Exception as e:
                logger.warning(
                    f"后台创建浏览器会话失败 (mid={mid}, browser_id={browser_id}, "
                    f"attempt={attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                else:
                    logger.exception(
                        f"后台创建浏览器会话失败，已达最大重试次数 "
                        f"(mid={mid}, browser_id={browser_id}): {e}"
                    )

    @staticmethod
    def get_browser_session_status(
        mid: int, browser_id: int
    ) -> BrowserSessionStatusData:
        """
        获取浏览器会话的详细状态
        """
        session_key = LiveService._get_session_key(mid, browser_id)

        if session_key not in LiveService.browser_sessions:
            return BrowserSessionStatusData(
                session_exists=False,
                browser_running=False,
                lifecycle_state=SessionLifecycleState.TERMINATED,
                last_heartbeat=0,
                active_connections=0,
                video_streaming=False,
                manual_mode=False,
                created_at=0,
                expires_at=None,
                status="terminated",
                cleanup_policy={},
                message="会话不存在",
            )

        entry = LiveService.browser_sessions[session_key]

        # 确保向后兼容性
        created_at = getattr(entry, "created_at", entry.last_activity)
        lifecycle_state = getattr(
            entry, "lifecycle_state", SessionLifecycleState.ACTIVE
        )
        expires_at = getattr(entry, "expires_at", None)

        return BrowserSessionStatusData(
            session_exists=True,
            browser_running=entry.status != BrowserStatusEnum.STOPPED,
            lifecycle_state=lifecycle_state,
            last_heartbeat=entry.last_heartbeat,
            active_connections=len(entry.active_connections),
            video_streaming=session_key in LiveService.live_streams,
            manual_mode=entry.is_manual_mode,
            created_at=created_at,
            expires_at=expires_at,
            status=entry.status.value,
            cleanup_policy={
                "max_idle_time": entry.cleanup_policy.max_idle_time,
                "max_no_heartbeat_time": entry.cleanup_policy.max_no_heartbeat_time,
                "cleanup_interval": entry.cleanup_policy.cleanup_interval,
            },
            message="会话状态正常",
        )


__all__ = [
    "LiveService",
    "RPAOperationService",
    "VideoStreamService",
]
