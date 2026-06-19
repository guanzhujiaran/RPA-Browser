"""
LiveService - 核心业务逻辑服务

此模块包含浏览器会话管理、心跳检测、人工操作干预等核心业务逻辑。
"""
from botright.playwright_mock.page import Page
from app.services.RPA_browser.browser_session_pool.playwright_pool import PlaywrightSessionPool
import time
import asyncio
import contextlib
from dataclasses import dataclass
from typing import Dict
from app.config import settings
from app.models.consts.enums import ConfigRunningModeEnum
from loguru import logger
from app.models.exceptions.base_exception import (
    BrowserNotStartedException,
    BrowserPageIndexError,
)
from app.models.runtime.control import (
    BrowserStatusEnum,
    BrowserCleanupPolicy,
    SessionLifecycleState,
    CreateSessionData,
    BrowserSessionStatusData,
)
from app.models.runtime.session import BrowserSessionRemoveParams

from app.models.runtime.live_service import (
    BrowserSessionEntry,
)
from app.models.runtime.session import SessionCreateParams
from app.services.RPA_browser.browser_session_pool.playwright_pool import (
    get_default_session_pool,
)
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    WebRTCEnabledSession,
)


@dataclass
class CleanupDecision:
    should_cleanup: bool = False
    reason: str = ""
    next_state: SessionLifecycleState = SessionLifecycleState.ACTIVE
    priority: int = 0  # 优先级，数字越小优先级越高


class LiveService:
    """浏览器控制服务类 - 支持人工干预、心跳检测和自动清理"""
    # 维护浏览器会话状态
    # key: f"{mid}_{browser_id}"
    # private属性，不允许直接操作
    _browser_sessions: Dict[str, BrowserSessionEntry] = {}
    # 默认配置
    DEFAULT_SESSION_TIMEOUT = 3600  # 1小时
    DEFAULT_CLEANUP_INTERVAL = 300  # 清理间隔5分钟
    # 🔑 添加会话级别的锁，防止并发操作导致的状态不一致
    _session_locks: Dict[str, asyncio.Lock] = {}
    _global_lock = asyncio.Lock()  # 用于保护 _session_locks 字典本身

    @staticmethod
    def _get_session_key(mid: int|str, browser_id: int|str) -> str:
        """获取会话键"""
        return f"{mid}_{browser_id}"

    async def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        """获取会话级别的锁（懒创建）"""
        async with self._global_lock:
            if session_key not in LiveService._session_locks:
                self._session_locks[session_key] = asyncio.Lock()
            return self._session_locks[session_key]

    async def _cleanup_session_lock(self, session_key: str):
        """清理会话锁（在会话删除后调用）"""
        async with self._global_lock:
            self._session_locks.pop(session_key, None)

    def _parse_session_key(self, session_key: str) -> tuple[int, int]:
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
    async def _check_session_cleanup(self):
        """检查会话清理 - 使用状态机判断会话清理"""
        current_time = int(time.time())
        sessions_to_cleanup = []

        # 🔑 第一阶段：收集需要清理的会话（不加锁，快速扫描）
        for session_key, entry in list(self._browser_sessions.items()):
            # 使用状态机评估会话状态
            cleanup_decision: CleanupDecision = self._evaluate_session_cleanup(
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
                mid, browser_id = self._parse_session_key(session_key)
                # 释放浏览器会话（内部会获取锁）
                await self.release_browser_session(mid, browser_id)
                logger.info(f"已清理会话: {session_key}, 原因: {decision.reason}")
            except Exception as e:
                logger.error(f"清理会话失败: {session_key}, error: {e}")

    def _evaluate_session_cleanup(self,
                                  entry: BrowserSessionEntry, current_time: int
                                  ) -> CleanupDecision:
        """
        评估会话是否需要清理 - 状态机核心逻辑

        优先级顺序（从高到低）:
        1. 过期时间检查 (expires_at)
        2. 闲置时间检查 (idle timeout)
        3. 直播流超时检查 (live stream timeout)

        Returns:
            CleanupDecision: 清理决策，包含是否清理、原因、下一个状态
        """

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

        # === 优先级 2: 检查闲置超时 ===
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
        if entry.lifecycle_state == SessionLifecycleState.IDLE and entry.status == BrowserStatusEnum.RUNNING:
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

    def get_browser_session_entry(
        self,
        mid: int|str,
        browser_id: int |str,
    ) -> BrowserSessionEntry:
        session_key = self._get_session_key(mid, browser_id)
        if entry := self._browser_sessions.get(session_key):
            return entry
        raise BrowserNotStartedException()

    async def get_browser_session_page(self, mid: int, browser_id: int, page_index: int | None = None) -> Page:
        entry = self.get_browser_session_entry(mid, browser_id)
        all_pages = entry.browser_session.all_pages
        if page_index is None:
            return await entry.browser_session.get_current_page()
        if 0 <= page_index < len(all_pages):
            return all_pages[page_index]
        raise BrowserPageIndexError(page_index)

    async def get_or_create_browser_session_entry(
        self,
        mid: int,
        browser_id: int,
        headless: bool = False,
        is_create_browser: bool = True,
        max_retries: int = 2,  # ✅ 最大重试次数
    ) -> BrowserSessionEntry:
        """获取插件化浏览器会话（优化锁策略，支持并发创建）"""
        start_time = time.time()
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # ✅ 重试循环：处理浏览器在创建过程中被关闭的情况
        for attempt in range(max_retries + 1):
            try:
                return await self._do_get_or_create_session_entry(
                    mid, browser_id, headless, is_create_browser, current_time, start_time
                )
            except BrowserNotStartedException as e:
                if attempt < max_retries:
                    logger.warning(
                        f"浏览器创建失败，第 {attempt + 1} 次重试: {session_key}, error: {e}")
                    await asyncio.sleep(0.5)  # 短暂等待后重试
                    continue
                logger.error(f"浏览器创建失败，已达最大重试次数: {session_key}")
                raise e
        raise BrowserNotStartedException()

    async def _do_get_or_create_session_entry(
        self,
        mid: int,
        browser_id: int,
        headless: bool,
        is_create_browser: bool,
        current_time: int,
        start_time: float,
    ) -> BrowserSessionEntry:
        """
        执行实际的会话获取或创建逻辑

        此方法负责：
        1. 检查现有会话的有效性
        2. 委托给 PlaywrightSessionPool._create_session 进行创建
        3. 在 LiveService.browser_sessions 中注册新创建的会话
        """
        session_key = self._get_session_key(mid, browser_id)
        pool: PlaywrightSessionPool = get_default_session_pool()
        # 🔑 第一阶段：检查现有会话
        if session_key in self._browser_sessions:
            entry = self._browser_sessions[session_key]

            # 验证浏览器是否真正运行
            if entry.browser_running:
                # 浏览器仍然可用，更新活动时间
                entry.last_activity = current_time
                elapsed = time.time() - start_time
                logger.debug(f"复用现有会话: {session_key}, 耗时: {elapsed:.3f}s")
                return entry

        # 🔑 第二阶段：如果不需要创建，抛出异常
        if not is_create_browser:
            raise BrowserNotStartedException()

        # 🔑 第三阶段：使用会话级别的锁保护创建过程
        lock = await self._get_session_lock(session_key)
        async with lock:
            # 双重检查：验证会话是否已被其他请求创建
            if session_key in self._browser_sessions:
                entry = self.get_browser_session_entry(mid, browser_id)
                if entry.browser_running:
                    entry.last_activity = current_time
                    elapsed = time.time() - start_time
                    logger.debug(
                        f"并发检查后发现会话已存在: {session_key}, 耗时: {elapsed:.3f}s")
                    return entry

            # 🔑 第四阶段：委托给 PlaywrightSessionPool 创建会话
            session_params = SessionCreateParams(
                mid=mid,
                browser_id=browser_id,
                headless=headless,
            )
            try:
                # 这里用get_session就行了，不存在自动创建
                browser_session = await pool.get_session(session_params)
                create_elapsed = time.time() - start_time
                logger.info(
                    f"浏览器创建完成: {session_key}, 耗时: {create_elapsed:.3f}s")

                # 🔑 第五阶段：验证刚创建的浏览器是否仍然有效
                if browser_session.is_closed:
                    logger.warning(f"刚创建的浏览器已关闭，清理并重新创建: {session_key}")
                    raise BrowserNotStartedException("浏览器在创建过程中被关闭，请重试")

                # 🔑 第六阶段：在 LiveService 中注册会话条目
                entry = BrowserSessionEntry(
                    mid=mid,
                    browser_id=browser_id,
                    browser_session=browser_session,
                    last_activity=current_time,
                )

                self._browser_sessions[session_key] = entry
                elapsed = time.time() - start_time
                logger.info(f"会话创建并注册完成: {session_key}, 总耗时: {elapsed:.3f}s")
                return entry
            except Exception as e:
                logger.exception(f"创建浏览器会话失败: {session_key}, error: {e}")
                raise e

    async def release_browser_session(self, mid: int, browser_id: int) -> bool:
        """释放浏览器会话（带锁保护）"""
        session_key = LiveService._get_session_key(mid, browser_id)

        try:
            # 🔑 获取会话级别的锁，防止并发操作
            lock = await self._get_session_lock(session_key)
            async with lock:
                pool = get_default_session_pool()

                # 关闭浏览器会话
                if session_key in self._browser_sessions:
                    entry = self.get_browser_session_entry(mid, browser_id)
                    # 🔑 关键：先关闭浏览器会话，再删除引用
                    with contextlib.suppress(Exception):
                        await entry.browser_session.close()
                    # 删除会话引用
                    del self._browser_sessions[session_key]
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
            await self._cleanup_session_lock(session_key)

            return True

        except Exception as e:
            logger.error(
                f"释放浏览器会话失败 (mid={mid}, browser_id={browser_id}): {e}"
            )
            return False

    @staticmethod
    async def create_browser_session(
        service: "LiveService",
        mid: int,
        browser_id: int
    ) -> CreateSessionData:
        """
        创建浏览器会话

        这是一个独立的会话创建接口，与心跳机制完全解耦。
        只有显式调用此接口才会创建浏览器会话。
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # 🔑 快速检查（不加锁）
        if session_key in service._browser_sessions:
            entry = service.get_browser_session_entry(mid, browser_id)

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
            entry = await service.get_or_create_browser_session_entry(
                mid, browser_id
            )
            # 显式确认浏览器会话状态为 RUNNING、生命周期为 ACTIVE
            entry.status = BrowserStatusEnum.RUNNING
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

    def get_browser_session_status(
        self,
        mid: int,
        browser_id: int
    ) -> BrowserSessionStatusData:
        """
        获取浏览器会话的详细状态
        """
        session_key = LiveService._get_session_key(mid, browser_id)

        if session_key not in self._browser_sessions:
            return BrowserSessionStatusData(
                session_exists=False,
                browser_running=False,
                lifecycle_state=SessionLifecycleState.TERMINATED,
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

        entry = self.get_browser_session_entry(mid, browser_id)
        screen_height = entry.browser_session.fingerprint_params.patchright_screen_height
        screen_width = entry.browser_session.fingerprint_params.patchright_screen_width
        viewport_width = entry.browser_session.fingerprint_params.patchright_viewport_width
        viewport_height = entry.browser_session.fingerprint_params.patchright_viewport_height

        # 确保向后兼容性
        created_at = entry.created_at
        lifecycle_state = entry.lifecycle_state
        expires_at = entry.calculated_expires_at
        browser_running = entry.browser_running

        return BrowserSessionStatusData(
            session_exists=True,
            browser_running=browser_running,
            lifecycle_state=lifecycle_state,
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

    async def ensure_webrtc_session(self, mid: int, browser_id: int, headless: bool = False) -> BrowserSessionEntry:
        """
        获取或创建带 WebRTC 能力的浏览器会话。

        WebRTC 管理器在会话创建时已自动初始化，无需额外 enable 调用。

        - 如果会话已存在：直接返回
        - 如果会话不存在：创建新会话（WebRTC 自动可用）

        Args:
            mid: 用户 ID
            browser_id: 浏览器指纹 ID
            headless: 是否无头模式

        Returns:
            BrowserSessionEntry: 浏览器会话条目（WebRTC 已就绪）
        """
        session_key = LiveService._get_session_key(mid, browser_id)

        # 检查是否已存在会话
        if session_key in LiveService._browser_sessions:
            return self.get_browser_session_entry(mid, browser_id)

        # 使用标准的 get_or_create 创建会话（WebRTC 管理器自动初始化）
        entry = await self.get_or_create_browser_session_entry(
            mid, browser_id, headless, is_create_browser=True
        )

        logger.info(f"WebRTC 就绪会话已创建: {session_key}")
        return entry


live_service = LiveService()

__all__ = [
    "live_service",
]
