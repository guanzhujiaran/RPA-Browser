"""
LiveService - 核心业务逻辑服务

此模块包含浏览器会话管理、心跳检测、人工操作干预等核心业务逻辑。
同时维护对 RPAOperationService 的引用以保持向后兼容。
"""
import time
import asyncio
import contextlib
from dataclasses import dataclass
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
    ManualOperationResult,
    AutomationResult,
    OperationStatusData,
    SessionStatisticsData,
    CreateSessionData,
    BrowserSessionStatusData,
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
)
from app.models.runtime.session import SessionCreateParams
from app.services.RPA_browser.browser_session_pool.playwright_pool import (
    get_default_session_pool,
)
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    PluginedSessionInfo,
)
from app.services.RPA_browser.browser_session_pool.webrtc_session import (
    WebRTCEnabledSession,
)
from app.services.RPA_browser.rpa_operation_service import RPAOperationService


class LiveService:
    """浏览器控制服务类 - 支持人工干预、心跳检测和自动清理"""

    # 维护浏览器会话状态
    browser_sessions: Dict[str, BrowserSessionEntry] = {}  # key: f"{mid}_{browser_id}"
    # 默认配置
    DEFAULT_SESSION_TIMEOUT = 3600  # 1小时
    DEFAULT_HEARTBEAT_INTERVAL = 30  # 心跳间隔30秒
    DEFAULT_CLEANUP_INTERVAL = 300  # 清理间隔5分钟
    
    # 🔑 添加会话级别的锁，防止并发操作导致的状态不一致
    _session_locks: Dict[str, asyncio.Lock] = {}
    _global_lock = asyncio.Lock()  # 用于保护 _session_locks 字典本身

    # 向后兼容：引用分离的服务
    RPAOperationService = RPAOperationService

    @staticmethod
    def _get_session_key(mid: int, browser_id: int) -> str:
        """获取会话键"""
        return f"{mid}_{browser_id}"
    
    @staticmethod
    async def _get_session_lock(session_key: str) -> asyncio.Lock:
        """获取会话级别的锁（懒创建）"""
        async with LiveService._global_lock:
            if session_key not in LiveService._session_locks:
                LiveService._session_locks[session_key] = asyncio.Lock()
            return LiveService._session_locks[session_key]
    
    @staticmethod
    async def _cleanup_session_lock(session_key: str):
        """清理会话锁（在会话删除后调用）"""
        async with LiveService._global_lock:
            LiveService._session_locks.pop(session_key, None)

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

        # 🔑 第一阶段：收集需要清理的会话（不加锁，快速扫描）
        for session_key, entry in list(LiveService.browser_sessions.items()):
            # 清理过期的心跳客户端（超过最大无心跳时间的客户端）
            expired_clients = [
                client_id
                for client_id, last_heartbeat in entry.heartbeat_clients.items()
                if current_time - last_heartbeat > entry.cleanup_policy.max_no_heartbeat_time
            ]
            
            for client_id in expired_clients:
                entry.heartbeat_clients.pop(client_id, None)
                entry.active_connections.discard(client_id)
                logger.debug(
                    f"会话 {session_key} 清理过期客户端: {client_id}, "
                    f"最后心跳: {last_heartbeat}, 当前时间: {current_time}"
                )
            
            # 🔑 注意：已迁移到 SSE 方案，不再需要清理 WebRTC 连接
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

        # 🔑 第二阶段：执行清理（每个会话单独加锁）
        for session_key, decision in sessions_to_cleanup:
            try:
                mid, browser_id = LiveService._parse_session_key(session_key)
                
                # 释放浏览器会话（内部会获取锁）
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
        @dataclass
        class CleanupDecision:
            should_cleanup: bool = False
            reason: str = ""
            next_state: SessionLifecycleState = SessionLifecycleState.ACTIVE
            priority: int = 0  # 优先级，数字越小优先级越高
        
        policy = entry.cleanup_policy
        decision = CleanupDecision()
        
        # === 优先级 1: 检查是否已过期 (expires_at) ===
        if entry.is_expired:
            return CleanupDecision(
                should_cleanup=True,
                reason="会话已过期",
                next_state=SessionLifecycleState.TERMINATING,
                priority=1
            )
        
        # === 优先级 2: 检查心跳超时 ===
        time_since_last_heartbeat = entry.heartbeat_duration
        has_active_clients = entry.has_active_clients
        
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
        time_since_last_activity = entry.idle_duration
        is_idle = entry.is_idle
        no_active_connections = entry.no_active_connections
        
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



    @staticmethod
    async def handle_heartbeat(
        mid: int, browser_id: int, heartbeat: HeartbeatRequest
    ) -> HeartbeatResponse:
        """处理心跳请求（带锁保护）"""
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # 🔑 获取会话级别的锁
        lock = await LiveService._get_session_lock(session_key)
        async with lock:
            if session_key not in LiveService.browser_sessions:
                return HeartbeatResponse(
                    success=False,
                    server_timestamp=current_time,
                    next_heartbeat_interval=LiveService.DEFAULT_HEARTBEAT_INTERVAL,
                    status="session_not_found",
                )

            entry = LiveService.browser_sessions[session_key]

            # 🔑 关键修复：先清理该客户端的旧记录，避免重复计数
            if heartbeat.client_id in entry.heartbeat_clients:
                logger.debug(
                    f"更新客户端 {heartbeat.client_id} 的心跳时间 "
                    f"(之前: {entry.heartbeat_clients[heartbeat.client_id]}, 现在: {current_time})"
                )
            
            # 更新心跳时间（这会覆盖旧的时间戳，不会增加数量）
            entry.heartbeat_clients[heartbeat.client_id] = current_time
            entry.last_heartbeat = current_time
            entry.last_activity = current_time

            # ✅ 更新指定页面的 WebRTC 活跃时间
            if entry.has_webrtc():
                webrtc_mgr = entry.plugined_session.webrtc_manager
                webrtc_mgr.update_page_activity(heartbeat.page_id)

            # 更新活跃连接（使用集合去重，不会重复添加）
            entry.active_connections.add(heartbeat.client_id)
            
            # 🔑 计算真正的活跃客户端数：基于 WebRTC 视频流连接
            active_stream_count = 0
            if entry.has_webrtc():
                active_stream_count = entry.plugined_session.webrtc_active_streams

        return HeartbeatResponse(
            success=True,
            server_timestamp=current_time,
            next_heartbeat_interval=LiveService.DEFAULT_HEARTBEAT_INTERVAL,
            status="heartbeat_received",
            active_clients=active_stream_count,  # 🔑 使用视频流连接数作为活跃客户端数
        )

    @staticmethod
    async def start_manual_operation(
        mid: int, browser_id: int, request: ManualOperationRequest
    ) -> ManualOperationResult:
        """开始人工操作（优化锁策略）"""
        session_key = LiveService._get_session_key(mid, browser_id)

        # 🔑 快速检查会话是否存在（不加锁）
        if session_key not in LiveService.browser_sessions:
            # 创建默认会话请求
            default_request = CreateSessionRequest(
                headless=False,
                auto_cleanup=True,
            )
            await LiveService.create_browser_session(mid, browser_id, default_request)

        # 🔑 获取会话级别的锁
        lock = await LiveService._get_session_lock(session_key)
        async with lock:
            # 双重检查
            if session_key not in LiveService.browser_sessions:
                return ManualOperationResult(
                    success=False,
                    message="会话创建失败",
                    status="error",
                    priority=0,
                    start_time=0,
                )

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
            
            # 更新状态为手动模式
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
        """恢复自动化任务（带锁保护）"""
        session_key = LiveService._get_session_key(mid, browser_id)

        # 🔑 获取会话级别的锁
        lock = await LiveService._get_session_lock(session_key)
        async with lock:
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

    @staticmethod
    async def get_or_create_browser_session(
        mid: int,
        browser_id: int,
        headless: bool=False,
        is_create_browser: bool=True,
        max_retries: int = 2,  # ✅ 最大重试次数
    ) -> PluginedSessionInfo:
        """获取插件化浏览器会话（优化锁策略，支持并发创建）"""
        start_time = time.time()
        
        pool = get_default_session_pool()
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # ✅ 重试循环：处理浏览器在创建过程中被关闭的情况
        for attempt in range(max_retries + 1):
            try:
                return await LiveService._do_get_or_create_session(
                    mid, browser_id, headless, is_create_browser, 
                    pool, session_key, current_time, start_time
                )
            except BrowserNotStartedException as e:
                if attempt < max_retries:
                    logger.warning(f"浏览器创建失败，第 {attempt + 1} 次重试: {session_key}, error: {e}")
                    await asyncio.sleep(0.5)  # 短暂等待后重试
                    continue
                else:
                    logger.error(f"浏览器创建失败，已达最大重试次数: {session_key}")
                    raise

    @staticmethod
    async def _do_get_or_create_session(
        mid: int,
        browser_id: int,
        headless: bool,
        is_create_browser: bool,
        pool,
        session_key: str,
        current_time: int,
        start_time: float,
    ) -> PluginedSessionInfo:
        """
        执行实际的会话获取或创建逻辑
        
        此方法负责：
        1. 检查现有会话的有效性
        2. 委托给 PlaywrightSessionPool._create_session 进行创建
        3. 在 LiveService.browser_sessions 中注册新创建的会话
        """
        # 🔑 第一阶段：检查现有会话
        if session_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[session_key]
            
            # 验证浏览器是否真正运行
            if entry.browser_running:
                # 浏览器仍然可用，更新活动时间
                entry.last_activity = current_time
                elapsed = time.time() - start_time
                logger.debug(f"复用现有会话: {session_key}, 耗时: {elapsed:.3f}s")
                return entry.plugined_session
        
        # 🔑 第二阶段：如果不需要创建，抛出异常
        if not is_create_browser:
            raise BrowserNotStartedException()
        
        # 🔑 第三阶段：使用会话级别的锁保护创建过程
        lock = await LiveService._get_session_lock(session_key)
        async with lock:
            # 双重检查：验证会话是否已被其他请求创建
            if session_key in LiveService.browser_sessions:
                entry = LiveService.browser_sessions[session_key]
                if entry.browser_running:
                    entry.last_activity = current_time
                    elapsed = time.time() - start_time
                    logger.debug(f"并发检查后发现会话已存在: {session_key}, 耗时: {elapsed:.3f}s")
                    return entry.plugined_session
            
            # 🔑 第四阶段：委托给 PlaywrightSessionPool 创建会话

            session_params = SessionCreateParams(
                mid=mid,
                browser_id=browser_id,
                headless=headless,
            )
            
            try:
                plugined_session = await pool._create_session(session_params)
                create_elapsed = time.time() - start_time
                logger.info(f"浏览器创建完成: {session_key}, 耗时: {create_elapsed:.3f}s")
                
                # 🔑 第五阶段：验证刚创建的浏览器是否仍然有效
                if plugined_session.is_closed:
                    logger.warning(f"刚创建的浏览器已关闭，清理并重新创建: {session_key}")
                    raise BrowserNotStartedException("浏览器在创建过程中被关闭，请重试")
                
                # 🔑 第六阶段：在 LiveService 中注册会话条目
                entry = BrowserSessionEntry(
                    mid=mid,
                    browser_id=browser_id,
                    plugined_session=plugined_session,
                    last_activity=current_time,
                    last_heartbeat=current_time,
                )
                
                LiveService.browser_sessions[session_key] = entry
                elapsed = time.time() - start_time
                logger.info(f"会话创建并注册完成: {session_key}, 总耗时: {elapsed:.3f}s")
                return plugined_session
            except Exception as e:
                logger.error(f"创建浏览器会话失败: {session_key}, error: {e}")
                raise

    @staticmethod
    async def execute_browser_command(
        mid: int, browser_id: int, command
    ) -> RPAResponse:
        """执行浏览器命令 - 支持优先级和人工操作检测（带锁保护）

        此方法通过统一的命令接口执行各种浏览器操作。

        Args:
            mid: 用户ID
            browser_id: 浏览器ID
            command: LiveControlCommand 对象或字典

        Returns:
            RPAResponse: 操作结果
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        
        # 🔑 获取会话级别的锁，防止并发操作导致状态不一致
        lock = await LiveService._get_session_lock(session_key)
        async with lock:
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

            # 如果命令需要中断自动化且当前处于自动化模式，则切换到手动模式
            if interrupt_automation and not entry.is_manual_mode:
                entry.is_manual_mode = True
                entry.status = BrowserStatusEnum.PAUSED
                entry.manual_operation_start_time = int(time.time())

        # 🔑 关键优化：在锁外执行实际的浏览器操作（耗时操作）
        # 锁只保护状态检查和修改，不保护实际的浏览器操作
        try:
            page = await entry.plugined_session.get_current_page()

            # 更新活动时间（在锁外更新，减少锁持有时间）
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
    async def release_browser_session(mid: int, browser_id: int) -> bool:
        """释放浏览器会话（带锁保护）"""
        session_key = LiveService._get_session_key(mid, browser_id)

        try:
            # 🔑 获取会话级别的锁，防止并发操作
            lock = await LiveService._get_session_lock(session_key)
            async with lock:
                pool = get_default_session_pool()

                # 🔑 注意：已迁移到 SSE 方案，不再需要关闭 WebRTC 连接

                # 关闭浏览器会话
                if session_key in LiveService.browser_sessions:
                    entry = LiveService.browser_sessions[session_key]
                    
                    # 🔑 关键：先关闭浏览器会话，再删除引用
                    with contextlib.suppress(Exception):
                        await entry.plugined_session.close()
                    
                    # 删除会话引用
                    del LiveService.browser_sessions[session_key]
                    logger.info(f"已删除会话: {session_key}")

                # 从池中释放会话
                remove_params = BrowserSessionRemoveParams(
                    mid=mid,
                    browser_id=browser_id,
                    force_close=True,  # 🔑 关键修复：强制关闭并删除浏览器实例，避免复用已关闭的浏览器
                )

                await pool.release_session(remove_params)
                logger.info(f"已从池中释放会话: mid={mid}, browser_id={browser_id}")
            
            # 🔑 在锁外清理会话锁（避免死锁）
            await LiveService._cleanup_session_lock(session_key)

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

        # 🔑 改进：使用 webrtc_connections 作为活跃连接数
        active_connections_count = len(entry.webrtc_connections)

        return BrowserStatus(
            mid=mid,
            browser_id=browser_id,
            status=entry.status,
            active_connections=active_connections_count,
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

        # 🔑 改进：统计所有会话的 WebRTC 连接数
        total_webrtc_connections = sum(
            len(entry.webrtc_connections)
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
            total_active_connections=total_webrtc_connections,
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

        # 🔑 快速检查（不加锁）
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
            # 🔑 优化：直接调用优化后的 get_or_create_browser_session
            await LiveService.get_or_create_browser_session(
                mid, browser_id
            )

            # 获取会话条目并设置生命周期状态
            entry = LiveService.browser_sessions[session_key]
            entry.lifecycle_state = SessionLifecycleState.ACTIVE
            
            # 从系统配置中读取过期时间
            expiration_time = settings.browser_session_expiration_time
            entry.expires_at = (
                current_time + expiration_time
                if expiration_time
                else None
            )

            # 从系统配置中读取清理策略
            if settings.browser_session_auto_cleanup:
                entry.cleanup_policy = BrowserCleanupPolicy(
                    max_idle_time=settings.browser_session_max_idle_time,
                    max_no_heartbeat_time=settings.browser_session_max_no_heartbeat_time,
                    cleanup_interval=settings.browser_session_cleanup_interval,
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
        session_key = LiveService._get_session_key(mid, browser_id)
        logger.info(f'[{session_key}]开始后台创建浏览器')
        for attempt in range(max_retries):
            try:
                # 🔑 快速检查（不加锁）
                if session_key in LiveService.browser_sessions:
                    logger.debug(f"会话已存在，跳过创建: {session_key}")
                    return

                # 🔑 优化：直接调用优化后的 get_or_create_browser_session
                await LiveService.get_or_create_browser_session(
                    mid, browser_id
                )

                # 获取会话条目并设置生命周期状态
                current_time = int(time.time())
                entry = LiveService.browser_sessions[session_key]
                entry.lifecycle_state = SessionLifecycleState.ACTIVE
                
                # 从系统配置中读取过期时间
                expiration_time = settings.browser_session_expiration_time
                entry.expires_at = (
                    current_time + expiration_time
                    if expiration_time
                    else None
                )

                # 从系统配置中读取清理策略
                if settings.browser_session_auto_cleanup:
                    entry.cleanup_policy = BrowserCleanupPolicy(
                        max_idle_time=settings.browser_session_max_idle_time,
                        max_no_heartbeat_time=settings.browser_session_max_no_heartbeat_time,
                        cleanup_interval=settings.browser_session_cleanup_interval,
                    )

                logger.info(f"后台创建浏览器会话成功: {session_key}\n耗时:{time.time() - start_time}秒")
                return

            except Exception as e:
                logger.exception(
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
                cleanup_policy=BrowserCleanupPolicy(),
                message="会话不存在",
                screen_height=0,
                screen_width=0,
                viewport_width=0,
                viewport_height=0,
            )

        entry = LiveService.browser_sessions[session_key]
        screen_height = entry.plugined_session.fingerprint_params.patchright_screen_height
        screen_width = entry.plugined_session.fingerprint_params.patchright_screen_width
        viewport_width = entry.plugined_session.fingerprint_params.patchright_viewport_width
        viewport_height = entry.plugined_session.fingerprint_params.patchright_viewport_height
        
        # 确保向后兼容性
        created_at = entry.created_at
        lifecycle_state = entry.lifecycle_state
        expires_at = entry.calculated_expires_at
        browser_running = entry.browser_running

        return BrowserSessionStatusData(
            session_exists=True,
            browser_running=browser_running,
            lifecycle_state=lifecycle_state,
            last_heartbeat=entry.last_heartbeat,
            active_connections=0,
            video_streaming=False,
            manual_mode=entry.is_manual_mode,
            created_at=created_at,
            expires_at=expires_at,  # 🔑 使用动态计算的过期时间
            status=entry.status.value,
            cleanup_policy=entry.cleanup_policy,
            message="会话状态正常" if browser_running else "会话存在但浏览器未运行",
            screen_height=screen_height,
            screen_width=screen_width,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
        )

    @staticmethod
    async def create_webrtc_enabled_session(mid: int, browser_id: int, headless: bool = False) -> PluginedSessionInfo:
        """
        获取或启用 WebRTC 功能的浏览器会话
        
        - 如果会话不存在：创建新的会话并启用 WebRTC
        - 如果会话已存在：在现有会话上动态启用 WebRTC（不会关闭会话）
        
        Args:
            mid: 用户 ID
            browser_id: 浏览器指纹 ID
            headless: 是否无头模式
            
        Returns:
            PluginedSessionInfo: 已启用 WebRTC 的会话实例
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        
        # 检查是否已存在会话
        if session_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[session_key]
            
            # 动态启用 WebRTC（如果尚未启用）
            await entry.plugined_session.enable_webrtc()
            
            logger.info(f"WebRTC 已在会话 {session_key} 上启用")
            return entry.plugined_session
        
        # 会话不存在，创建新的并启用 WebRTC
        logger.info(f"创建新的会话并启用 WebRTC: mid={mid}, browser_id={browser_id}")
        
        # 创建普通的 PluginedSessionInfo
        session = await PluginedSessionInfo.new(mid, browser_id, headless)
        
        # 启用 WebRTC
        await session.enable_webrtc()
        
        # 创建 BrowserSessionEntry
        entry = BrowserSessionEntry(
            mid=mid,
            browser_id=browser_id,
            plugined_session=session
        )
        LiveService.browser_sessions[session_key] = entry
        
        logger.info(f"会话已创建并启用 WebRTC: {session_key}")
        return session


__all__ = [
    "LiveService",
    "RPAOperationService",
]
