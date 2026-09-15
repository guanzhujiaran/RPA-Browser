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
from app.models.common.exceptions.base_exception import (
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
    # 闲置动作: none | degrade | suspend | restore | terminate
    action: str = "none"


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

    # ── 活跃刷新 / 自动化占用（见 docs/be-message-统一计划书.md §5.15）──

    async def touch(self, mid: int | str, browser_id: int | str, *, source: str = "unknown") -> bool:
        """刷新会话活跃时间戳（真实操作入口调用）。

        语义：任何**真实操作**（HTTP 操作接口 / action 执行 / WebRTC 信令）都应调用本方法，
        使会话回到 ACTIVE、解除流降级并取消「待关闭」宽限。**状态查询类接口不应调用**，
        否则前端轮询会持续续命，导致闲置回收失效。

        Returns:
            bool: 会话存在并刷新成功返回 True
        """
        session_key = self._get_session_key(mid, browser_id)
        entry = self._browser_sessions.get(session_key)
        if entry is None:
            return False

        entry.last_activity = int(time.time())
        entry.terminate_scheduled_at = None
        entry.status = BrowserStatusEnum.RUNNING
        entry.lifecycle_state = SessionLifecycleState.ACTIVE

        # 解除流降级（幂等：未降级时为空操作；仅在真正恢复时重启一次 screencast）
        manager = getattr(entry.browser_session, "webrtc_manager", None)
        if manager is not None:
            try:
                await manager.set_degraded(False)
            except Exception as e:
                logger.debug(f"touch 解除降级失败: {session_key}, {e}")

        logger.debug(f"会话活跃刷新: {session_key} (source={source})")
        return True

    def pin(self, mid: int | str, browser_id: int | str) -> bool:
        """标记会话被自动化任务占用（占用期间禁止一切降级/关闭）。需与 unpin 配对。"""
        entry = self._browser_sessions.get(self._get_session_key(mid, browser_id))
        if entry is None:
            return False
        entry.pin_count += 1
        return True

    def unpin(self, mid: int | str, browser_id: int | str) -> bool:
        """解除自动化任务占用（与 pin 配对；计数归零后恢复可回收）。"""
        entry = self._browser_sessions.get(self._get_session_key(mid, browser_id))
        if entry is None:
            return False
        if entry.pin_count > 0:
            entry.pin_count -= 1
        return True

    async def _check_session_cleanup(self):
        """扫描所有会话并执行闲置生命周期动作（三级软着陆）。

        以 `entry.last_activity` 为唯一活跃时间戳（由 touch() 刷新）：
        - 被自动化任务 pin 住 → 跳过一切动作；
        - 闲置达降级阈值 → 降质降帧；
        - 闲置达挂起阈值 → 关流保实例；
        - 闲置达关实例阈值 → 进入宽限期，到期 release_browser_session()。
        """
        current_time = int(time.time())
        sessions_to_cleanup: list[tuple[str, CleanupDecision]] = []

        # 🔑 第一阶段：评估并就地应用非关闭动作（降级/挂起/恢复）
        for session_key, entry in list(self._browser_sessions.items()):
            decision = self._evaluate_session_cleanup(entry, current_time)

            if decision.action == "terminate":
                logger.warning(
                    f"会话 {session_key} 需要关闭 - 原因: {decision.reason}, "
                    f"状态: {entry.lifecycle_state.value} -> {decision.next_state.value}"
                )
                sessions_to_cleanup.append((session_key, decision))
                continue

            try:
                await self._apply_idle_action(entry, decision)
            except Exception as e:
                logger.error(
                    f"应用闲置动作失败: {session_key}, action={decision.action}, error: {e}"
                )

        # 🔑 第二阶段：执行关闭（每个会话单独加锁）
        for session_key, decision in sessions_to_cleanup:
            try:
                mid, browser_id = self._parse_session_key(session_key)
                # 释放浏览器会话（内部会获取锁）
                await self.release_browser_session(mid, browser_id)
                logger.info(f"已清理会话: {session_key}, 原因: {decision.reason}")
            except Exception as e:
                logger.error(f"清理会话失败: {session_key}, error: {e}")

    def _evaluate_session_cleanup(
        self, entry: BrowserSessionEntry, current_time: int
    ) -> CleanupDecision:
        """评估会话的闲置档位（纯函数，不修改 entry 状态）。

        优先级：自动化占用(pin) > 过期 > 关实例(含宽限) > 挂起 > 降级 > 正常。
        """
        policy = entry.cleanup_policy
        idle = entry.idle_duration

        # === 优先级 0: 自动化任务占用，禁止任何降级/关闭 ===
        if entry.is_pinned:
            return CleanupDecision(
                reason="自动化任务运行中(pin)",
                next_state=SessionLifecycleState.ACTIVE,
                priority=0,
                action="restore",
            )

        # === 优先级 1: 检查是否已过期 (expires_at) ===
        if entry.is_expired:
            return CleanupDecision(
                should_cleanup=True,
                reason="会话已过期",
                next_state=SessionLifecycleState.TERMINATING,
                priority=1,
                action="terminate",
            )

        # === 优先级 2: 闲置达关实例阈值（先宽限，再关闭）===
        if idle >= policy.max_idle_time:
            if entry.terminate_scheduled_at is None:
                entry.terminate_scheduled_at = current_time
                logger.warning(
                    f"会话闲置超时，进入 {settings.browser_session_terminate_grace}s "
                    f"宽限期后关闭 (idle={idle}s, max_idle_time={policy.max_idle_time}s)"
                )
                return CleanupDecision(
                    reason=f"闲置超时，{settings.browser_session_terminate_grace}s 宽限期",
                    next_state=SessionLifecycleState.TERMINATING,
                    priority=2,
                    action="suspend",
                )
            if current_time >= entry.terminate_scheduled_at + settings.browser_session_terminate_grace:
                return CleanupDecision(
                    should_cleanup=True,
                    reason=f"闲置超时 ({idle}s >= {policy.max_idle_time}s)",
                    next_state=SessionLifecycleState.TERMINATING,
                    priority=2,
                    action="terminate",
                )
            return CleanupDecision(
                reason="宽限期等待中",
                next_state=SessionLifecycleState.TERMINATING,
                priority=2,
                action="suspend",
            )

        # 未达关实例阈值：清空宽限标记（防止残留）
        entry.terminate_scheduled_at = None

        # === 优先级 3: 闲置达挂起阈值（关流保实例）===
        if idle >= settings.browser_stream_suspend_after:
            return CleanupDecision(
                reason=f"闲置挂起 ({idle}s >= {settings.browser_stream_suspend_after}s)",
                next_state=SessionLifecycleState.IDLE,
                priority=3,
                action="suspend",
            )

        # === 优先级 4: 闲置达降级阈值（降质降帧）===
        if idle >= settings.browser_stream_degrade_after:
            return CleanupDecision(
                reason=f"闲置降级 ({idle}s >= {settings.browser_stream_degrade_after}s)",
                next_state=SessionLifecycleState.ACTIVE,
                priority=4,
                action="degrade",
            )

        # === 优先级 5: 活跃 ===
        return CleanupDecision(
            reason="状态正常",
            next_state=SessionLifecycleState.ACTIVE,
            priority=99,
            action="restore",
        )

    async def _apply_idle_action(
        self, entry: BrowserSessionEntry, decision: CleanupDecision
    ):
        """就地应用闲置动作（降级/挂起/恢复）并同步 entry 生命周期状态。"""
        action = decision.action

        if action == "suspend":
            entry.status = BrowserStatusEnum.IDLE
        elif action in ("restore", "degrade"):
            entry.status = BrowserStatusEnum.RUNNING
        entry.lifecycle_state = decision.next_state

        manager = getattr(entry.browser_session, "webrtc_manager", None)
        if manager is None:
            return

        if action == "degrade":
            await manager.set_degraded(True)
            logger.info(f"会话闲置降级: mid={entry.mid}, browser_id={entry.browser_id}")
        elif action == "suspend":
            await manager.suspend_streams()
            logger.info(f"会话闲置挂起（关流保实例）: mid={entry.mid}, browser_id={entry.browser_id}")
        elif action == "restore":
            await manager.set_degraded(False)

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
                # 浏览器仍然可用，更新活动时间并复位闲置状态
                entry.last_activity = current_time
                entry.terminate_scheduled_at = None
                entry.status = BrowserStatusEnum.RUNNING
                entry.lifecycle_state = SessionLifecycleState.ACTIVE
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
                    entry.terminate_scheduled_at = None
                    entry.status = BrowserStatusEnum.RUNNING
                    entry.lifecycle_state = SessionLifecycleState.ACTIVE
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
            idle_seconds=entry.idle_duration,
            is_pinned=entry.is_pinned,
            pending_termination_at=(
                entry.terminate_scheduled_at + settings.browser_session_terminate_grace
                if entry.terminate_scheduled_at
                else None
            ),
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

        # 检查是否已存在会话：返回前刷新活跃（用户重新拉流即视为活跃，可解除挂起）
        if session_key in LiveService._browser_sessions:
            entry = self.get_browser_session_entry(mid, browser_id)
            await self.touch(mid, browser_id, source="webrtc_ensure")
            return entry

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
