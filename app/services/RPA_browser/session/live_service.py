"""
LiveService - 核心业务逻辑服务

此模块包含浏览器会话管理、心跳检测、人工操作干预等核心业务逻辑。
"""

from botright.playwright_mock.page import Page
from app.services.RPA_browser.browser_session_pool.playwright_pool import (
    PlaywrightSessionPool,
)
import time
import asyncio
import contextlib
from dataclasses import dataclass
from typing import Dict
from app.config import settings
from bili_common.models.response_code import ResponseCode
from app.models.consts.enums import ConfigRunningModeEnum
from loguru import logger
from app.models.common.exceptions.base_exception import (
    BrowserNotStartedException,
    BrowserLaunchQueueCancelledException,
    BrowserLaunchQueueTimeoutException,
    BrowserPageIndexError,
)
from app.models.runtime.control import (
    BrowserStatusEnum,
    BrowserCleanupPolicy,
    SessionLifecycleState,
    CreateSessionData,
    BrowserSessionStatusData,
    BrowserLaunchQueueStatusResponse,
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
from app.services.RPA_browser.session.launch_queue import (
    LaunchTicket,
    get_launch_queue,
)
from app.services.RPA_browser.session.session_status_bus import session_status_bus


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
    # 🔑 排队启动的后台任务引用（防止被 GC 回收）
    _background_launch_tasks: set[asyncio.Task] = set()
    # 🔑 SSE 状态推送去重：会话键 → 最近一次已推送的快照签名
    _last_status_signature: dict[str, str] = {}

    #: 参与「是否需要推送」判定的稳定字段。
    #: 刻意排除 idle_seconds / created_at / expires_at 等每请求都变的字段，
    #: 否则会退化成逐秒推送（详见 docs/rpa-会话状态SSE推送计划书.md §2.2）。
    _STATUS_SIGNATURE_FIELDS: tuple[str, ...] = (
        "session_exists",
        "browser_running",
        "status",
        "lifecycle_state",
        "is_pinned",
        "pending_termination_at",
        "in_launch_queue",
        "queue_state",
        "queue_type",
        "queue_position",
        # 观看者数变化（多人观看 / 有人离开）也要即时推送，前端据此展示「N 人在观看」
        "viewer_count",
        # 观看者列表变化（有人加入 / 离开 / 暂停 / 切档）同样即时推送，
        # 前端因此无需再轮询 /webrtc/status 取「谁在看」列表。
        # ⚠️ BrowserSessionViewerData 刻意不含 idle_seconds / last_activity，
        # 否则该字段每请求都变，会让去重失效、退化成逐秒推送。
        "viewers",
    )

    @staticmethod
    def _get_session_key(mid: int | str, browser_id: int | str) -> str:
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

    # ── 会话状态 SSE 推送（见 docs/rpa-会话状态SSE推送计划书.md）──

    def _notify_session_status(self, mid: int | str, browser_id: int | str) -> None:
        """向 SSE 订阅者推送最新会话状态快照。

        去重：只有 `_STATUS_SIGNATURE_FIELDS` 组成的签名变化时才推送，
        因此可以安全地挂在 touch 这类高频入口上（见计划书 §2.2）。
        无订阅者时直接返回，不构造快照。
        """
        mid_int = int(mid)
        browser_id_int = int(browser_id)
        if not session_status_bus.has_subscribers(mid_int, browser_id_int):
            return

        status = self.get_browser_session_status(mid_int, browser_id_int)
        signature = "|".join(
            str(getattr(status, field)) for field in self._STATUS_SIGNATURE_FIELDS
        )
        session_key = self._get_session_key(mid_int, browser_id_int)
        if self._last_status_signature.get(session_key) == signature:
            return

        self._last_status_signature[session_key] = signature
        session_status_bus.publish(mid_int, browser_id_int, status)

    def _forget_session_status(self, mid: int | str, browser_id: int | str) -> None:
        """会话已销毁：清掉签名缓存，避免键泄漏与后续复用时的误去重。"""
        self._last_status_signature.pop(self._get_session_key(mid, browser_id), None)

    def notify_session_status(self, mid: int | str, browser_id: int | str) -> None:
        """公开的状态推送入口 —— 供观看者生命周期 / 播放状态变化时调用。

        与 `touch()` 的区别：**只推送快照，不刷新活跃时间**。
        心跳下线（计划书 §10.7）后，观看者的加入 / 离开 / 暂停 / 切档
        不再有任何周期性 touch 兜底触发推送，必须在这些变化点上显式调用本方法，
        否则 SSE 订阅者会一直看到过期的人数与观看者列表。
        """
        self._notify_session_status(mid, browser_id)

    # ── 活跃刷新 / 自动化占用（见 docs/be-message-统一计划书.md §5.15）──

    async def touch(
        self, mid: int | str, browser_id: int | str, *, source: str = "unknown"
    ) -> bool:
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
        # 可能把挂起/降级/待关闭的会话拉回 ACTIVE，需推送给 SSE 订阅者（有签名去重）
        self._notify_session_status(mid, browser_id)
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

    def begin_workflow_run(
        self, mid: int | str, browser_id: int | str, run_id: str
    ) -> bool:
        """标记会话进入工作流执行期（执行期禁止调试类接口；直播不受影响，见 §5.17）。

        需与 ``end_workflow_run`` 配对。不复用 ``pin`` 是因为单步调试自身也会 pin，
        无法区分「工作流在执行」与「用户自己在调试」。

        Returns:
            bool: 会话存在并标记成功返回 True
        """
        entry = self._browser_sessions.get(self._get_session_key(mid, browser_id))
        if entry is None:
            return False
        entry.workflow_run_id = run_id
        return True

    def end_workflow_run(self, mid: int | str, browser_id: int | str) -> bool:
        """解除工作流执行期标记（与 ``begin_workflow_run`` 配对）。

        Returns:
            bool: 会话存在并解除成功返回 True
        """
        entry = self._browser_sessions.get(self._get_session_key(mid, browser_id))
        if entry is None:
            return False
        entry.workflow_run_id = None
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

        # === 优先级 2: 有 WebRTC 链路连通的观看者 → 视为活跃（豁免闲置类回收）===
        # 有人在看就不该闲置降级 / 挂起 —— 那本是「没人用」才触发的动作。
        # 活性以 ICE/DTLS 连接状态（pc.connectionState）为准而非 HTTP 心跳：
        # 暂停中的观看者保活仍在；监管页不订阅会话状态 SSE，也照样被覆盖。
        # 放在「过期」之后：到期是硬约束，有人在看也要关（见计划书 §10.7）。
        manager = getattr(entry.browser_session, "webrtc_manager", None)
        if manager is not None and manager.has_live_viewer():
            return CleanupDecision(
                reason="有观看者正在观看(WebRTC 连通)",
                next_state=SessionLifecycleState.ACTIVE,
                priority=2,
                action="restore",
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
            if (
                current_time
                >= entry.terminate_scheduled_at
                + settings.browser_session_terminate_grace
            ):
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
        # 降级/挂起/恢复都是服务端主动触发的状态变化，需即时推送给 SSE 订阅者
        self._notify_session_status(entry.mid, entry.browser_id)

        manager = getattr(entry.browser_session, "webrtc_manager", None)
        if manager is None:
            return

        if action == "degrade":
            await manager.set_degraded(True)
            logger.info(f"会话闲置降级: mid={entry.mid}, browser_id={entry.browser_id}")
        elif action == "suspend":
            await manager.suspend_streams()
            logger.info(
                f"会话闲置挂起（关流保实例）: mid={entry.mid}, browser_id={entry.browser_id}"
            )
        elif action == "restore":
            await manager.set_degraded(False)

    def get_browser_session_entry(
        self,
        mid: int | str,
        browser_id: int | str,
    ) -> BrowserSessionEntry:
        session_key = self._get_session_key(mid, browser_id)
        if entry := self._browser_sessions.get(session_key):
            return entry
        raise BrowserNotStartedException()

    def find_session_entry_by_browser_id(
        self, browser_id: int | str
    ) -> BrowserSessionEntry | None:
        """按 browser_id 反查会话条目（监管管理员观看场景）。

        会话表的键是 `{mid}_{browser_id}`。监管管理员访问**他人**浏览器时，请求里带的是
        **管理员自己的 mid**，拼不出归属者的键，因此只能按 browser_id 反查
        （browser_id 全局唯一，结果唯一）。

        ⚠️ 调用方必须已通过 `verify_browser_ownership_or_admin`（归属校验或监管权限校验），
        否则等于越权读取他人会话。

        Returns:
            BrowserSessionEntry；不存在返回 None
        """
        try:
            target = int(browser_id)
        except (TypeError, ValueError):
            return None
        for entry in self._browser_sessions.values():
            if entry.browser_id == target:
                return entry
        return None

    async def get_browser_session_page(
        self, mid: int, browser_id: int, page_index: int | None = None
    ) -> Page:
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
        is_vip: bool = False,  # VIP 身份：决定进入哪条启动队列
    ) -> BrowserSessionEntry:
        """获取插件化浏览器会话（优化锁策略，支持并发创建）"""
        start_time = time.time()
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # ✅ 重试循环：处理浏览器在创建过程中被关闭的情况
        for attempt in range(max_retries + 1):
            try:
                return await self._do_get_or_create_session_entry(
                    mid,
                    browser_id,
                    headless,
                    is_create_browser,
                    current_time,
                    start_time,
                    is_vip,
                )
            except BrowserNotStartedException as e:
                if attempt < max_retries:
                    logger.warning(
                        f"浏览器创建失败，第 {attempt + 1} 次重试: {session_key}, error: {e}"
                    )
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
        is_vip: bool = False,
    ) -> BrowserSessionEntry:
        """执行实际的会话获取或创建逻辑，并统一释放内存准入预留

        无论走「复用现有会话」还是「新建会话」，方法返回/抛错前都会调用
        ``launch_settled`` 释放启动队列的内存预留额度（幂等：无凭证时为空操作），
        避免调用方先 ``reserve`` 后因复用分支提前返回而造成额度泄漏。
        """
        try:
            return await self._reuse_or_create_session_entry(
                mid,
                browser_id,
                headless,
                is_create_browser,
                current_time,
                start_time,
                is_vip,
            )
        finally:
            await get_launch_queue().launch_settled(mid, browser_id)

    async def _reuse_or_create_session_entry(
        self,
        mid: int,
        browser_id: int,
        headless: bool,
        is_create_browser: bool,
        current_time: int,
        start_time: float,
        is_vip: bool = False,
    ) -> BrowserSessionEntry:
        """
        会话获取/创建主体：

        1. 检查现有会话的有效性
        2. 内存准入排队（内存不足时按 VIP / 普通队列等待）
        3. 委托给 PlaywrightSessionPool._create_session 进行创建
        4. 在 LiveService.browser_sessions 中注册新创建的会话
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
                self._notify_session_status(mid, browser_id)
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
                        f"并发检查后发现会话已存在: {session_key}, 耗时: {elapsed:.3f}s"
                    )
                    self._notify_session_status(mid, browser_id)
                    return entry

            # 🔑 第四阶段：委托给 PlaywrightSessionPool 创建会话
            session_params = SessionCreateParams(
                mid=mid,
                browser_id=browser_id,
                headless=headless,
            )
            launch_queue = get_launch_queue()
            try:
                # 🔑 内存准入：内存不足时在此排队等待（VIP 队列优先），超时抛错
                await launch_queue.acquire(mid, browser_id, is_vip=is_vip)
                # 这里用get_session就行了，不存在自动创建
                browser_session = await pool.get_session(session_params)
                create_elapsed = time.time() - start_time
                logger.info(
                    f"浏览器创建完成: {session_key}, 耗时: {create_elapsed:.3f}s"
                )

                # 🔑 第五阶段：验证刚创建的浏览器是否仍然有效
                if browser_session.is_closed:
                    logger.warning(
                        f"刚创建的浏览器已关闭，清理并重新创建: {session_key}"
                    )
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
                logger.info(
                    f"会话创建并注册完成: {session_key}, 总耗时: {elapsed:.3f}s"
                )
                # 会话从「不存在」变为「存在」，即时推送给 SSE 订阅者
                self._notify_session_status(mid, browser_id)
                return entry
            except Exception as e:
                logger.exception(f"创建浏览器会话失败: {session_key}, error: {e}")
                raise e

    async def release_browser_session(self, mid: int, browser_id: int) -> bool:
        """释放浏览器会话（带锁保护）"""
        session_key = LiveService._get_session_key(mid, browser_id)
        launch_queue = get_launch_queue()

        # 🔑 若该会话仍在启动队列中排队，先取消，避免「关掉又被拉起」
        await launch_queue.cancel(mid, browser_id)

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
                    # 会话已销毁：先推送「不存在」快照，再清掉签名缓存
                    self._notify_session_status(mid, browser_id)
                    self._forget_session_status(mid, browser_id)

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
            # 🔑 内存已释放，唤醒排队中的启动请求（VIP 优先）
            await launch_queue.notify_capacity_freed()

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
        browser_id: int,
        is_vip: bool = False,
    ) -> CreateSessionData:
        """
        创建浏览器会话

        这是一个独立的会话创建接口，与心跳机制完全解耦。
        只有显式调用此接口才会创建浏览器会话。

        内存准入：
        - 内存充足 → 同步创建并返回 running；
        - 内存不足 → 进入启动队列（VIP 队列优先），立即返回 queued + 排位，
          真正的创建放到后台任务中执行，前端通过会话状态接口轮询进度。
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

        launch_queue = get_launch_queue()

        # 🔑 先同步取号：内存充足则直接放行，否则进入队列（VIP 优先）
        ticket = launch_queue.reserve(mid, browser_id, is_vip=is_vip)

        # 🔑 内存不足 / 队列有人等待：转入后台排队启动，立即返回排队信息
        if not ticket.admitted:
            entry_status = launch_queue.get_entry_status(mid, browser_id)
            queue_label = "VIP" if is_vip else "普通"
            logger.info(
                f"内存不足，浏览器启动进入{queue_label}队列: {session_key}, "
                f"排位={entry_status.position}"
            )
            # 后台任务会等待同一凭证被放行后创建浏览器
            service._spawn_background_launch(mid, browser_id, is_vip, ticket)
            return CreateSessionData(
                success=True,
                session_id=session_key,
                browser_started=False,
                created_at=current_time,
                expires_at=None,
                queued=True,
                queue_type=entry_status.queue_type,
                queue_position=entry_status.position,
                message=f"当前服务器内存不足，已进入{queue_label}队列排队，请稍候",
            )

        try:
            # 🔑 已放行：直接调用优化后的 get_or_create_browser_session
            entry = await service.get_or_create_browser_session_entry(
                mid, browser_id, is_vip=is_vip
            )
            # 显式确认浏览器会话状态为 RUNNING、生命周期为 ACTIVE
            entry.status = BrowserStatusEnum.RUNNING
            entry.lifecycle_state = SessionLifecycleState.ACTIVE

            # 从系统配置中读取过期时间
            expiration_time = settings.browser_session_expiration_time
            entry.expires_at = (
                current_time + expiration_time if expiration_time else None
            )

            # 从系统配置中读取清理策略
            if settings.browser_session_auto_cleanup:
                entry.cleanup_policy = BrowserCleanupPolicy(
                    max_idle_time=settings.browser_session_max_idle_time,
                    cleanup_interval=settings.browser_session_cleanup_interval,
                )

            # 启动已落地（此时排队凭证已结算），推送最终快照
            service._notify_session_status(mid, browser_id)

            return CreateSessionData(
                success=True,
                session_id=session_key,
                browser_started=True,
                created_at=entry.created_at,
                expires_at=entry.expires_at,
                message="浏览器会话创建成功",
            )

        except BrowserLaunchQueueTimeoutException as e:
            # 排队超时：内存长时间未释放，返回业务错误码供前端提示
            logger.warning(f"浏览器启动排队超时: {session_key}, {e.msg}")
            return CreateSessionData(
                success=False,
                session_id=session_key,
                browser_started=False,
                created_at=0,
                expires_at=None,
                error=e.msg,
                error_code=int(e.code) if e.code is not None else None,
            )
        except Exception as e:
            return CreateSessionData(
                success=False,
                session_id=session_key,
                browser_started=False,
                created_at=0,
                expires_at=None,
                error=f"创建会话失败: {str(e)}",
                error_code=ResponseCode.INTERNAL_ERROR,
            )

    def _spawn_background_launch(
        self, mid: int, browser_id: int, is_vip: bool, ticket: LaunchTicket
    ) -> None:
        """把排队启动放到后台任务（请求立即返回 queued）"""
        task = asyncio.create_task(
            self._background_launch(mid, browser_id, is_vip, ticket)
        )
        LiveService._background_launch_tasks.add(task)
        task.add_done_callback(LiveService._background_launch_tasks.discard)

    async def _background_launch(
        self, mid: int, browser_id: int, is_vip: bool, ticket: LaunchTicket
    ) -> None:
        """后台等待内存放行并创建浏览器会话

        必须复用调用方已取号的凭证：否则「排队期间被取消」会在后台被重新取号，
        导致取消后又被拉起。
        """
        session_key = self._get_session_key(mid, browser_id)
        try:
            await get_launch_queue().wait(ticket)
            entry = await self.get_or_create_browser_session_entry(
                mid, browser_id, is_vip=is_vip
            )
            entry.status = BrowserStatusEnum.RUNNING
            entry.lifecycle_state = SessionLifecycleState.ACTIVE
            logger.info(f"排队会话启动完成: {session_key}")
            # 排队放行 → 启动完成：推送最终快照（含已退出的排队字段）
            self._notify_session_status(mid, browser_id)
        except BrowserLaunchQueueCancelledException:
            logger.info(f"排队会话已被取消: {session_key}")
        except BrowserLaunchQueueTimeoutException as e:
            logger.warning(f"排队会话启动超时: {session_key}, {e.msg}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"排队会话启动失败: {session_key}, error: {e}")

    def would_queue_browser_session(
        self, mid: int, browser_id: int, is_vip: bool = False
    ) -> bool:
        """该会话此刻启动是否需要排队（会话已存在时恒为 False）

        供 pages / webrtc 等「顺手拉起浏览器」的入口做前置判断，
        避免内存不足时长时间阻塞 HTTP 请求。
        """
        if self._get_session_key(mid, browser_id) in self._browser_sessions:
            return False
        return get_launch_queue().would_queue(mid, browser_id, is_vip=is_vip)

    def get_launch_queue_status(
        self, mid: int, browser_id: int
    ) -> BrowserLaunchQueueStatusResponse:
        """查询启动排队状态：全局队列水位 + 当前会话排位

        供前端展示「排队中，前方还有 N 位」等进度信息；
        会话未在排队时 ``in_queue`` 为 False，其余全局字段仍然可用。
        """
        launch_queue = get_launch_queue()
        queue_status = launch_queue.get_status()
        entry_status = launch_queue.get_entry_status(mid, browser_id)

        return BrowserLaunchQueueStatusResponse(
            enabled=queue_status.enabled,
            vip_waiting=queue_status.vip_waiting,
            normal_waiting=queue_status.normal_waiting,
            launching=queue_status.launching,
            max_wait_seconds=settings.browser_launch_queue_max_wait_time,
            max_instances=queue_status.max_instances,
            memory=queue_status.memory,
            browser_memory=queue_status.browser_memory,
            in_queue=entry_status.in_queue,
            queue_state=entry_status.state,
            queue_type=entry_status.queue_type,
            queue_position=entry_status.position,
            queue_waiting_seconds=entry_status.waiting_seconds,
            estimated_wait_seconds=entry_status.estimated_wait_seconds,
            estimate_reliable=entry_status.estimate_reliable,
        )

    def get_browser_session_status(
        self, mid: int, browser_id: int
    ) -> BrowserSessionStatusData:
        """
        获取浏览器会话的详细状态
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        queue_status = get_launch_queue().get_entry_status(mid, browser_id)

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
                status="queued" if queue_status.in_queue else "terminated",
                cleanup_policy=BrowserCleanupPolicy(),
                message=(
                    "会话正在启动队列中排队" if queue_status.in_queue else "会话不存在"
                ),
                screen_height=0,
                screen_width=0,
                viewport_width=0,
                viewport_height=0,
                in_launch_queue=queue_status.in_queue,
                queue_state=queue_status.state,
                queue_type=queue_status.queue_type,
                queue_position=queue_status.position,
                queue_waiting_seconds=queue_status.waiting_seconds,
            )

        entry = self.get_browser_session_entry(mid, browser_id)
        screen_height = (
            entry.browser_session.fingerprint_params.patchright_screen_height
        )
        screen_width = entry.browser_session.fingerprint_params.patchright_screen_width
        viewport_width = (
            entry.browser_session.fingerprint_params.patchright_viewport_width
        )
        viewport_height = (
            entry.browser_session.fingerprint_params.patchright_viewport_height
        )

        # 确保向后兼容性
        created_at = entry.created_at
        lifecycle_state = entry.lifecycle_state
        expires_at = entry.calculated_expires_at
        browser_running = entry.browser_running

        # 观看者连接数（多观看者并发直播）：取不到管理器时为 0。
        # 用对外计数（排除监管管理员观看者）—— 管理员观看对归属者完全隐藏，
        # 不能体现在人数里（见 docs/rpa-多观看者并发直播计划书.md §2.7）。
        manager = getattr(entry.browser_session, "webrtc_manager", None)
        viewer_count = manager.public_viewer_count if manager is not None else 0

        # 观看者摘要随状态一并下发（SSE 首帧即全量快照），前端因此**不再轮询**
        # /webrtc/status 取列表。构造收敛在 manager.viewer_summaries()：
        # 与 /webrtc/status 共用同一份，两条出口字段一致（见计划书 §4.4）。
        viewers = manager.viewer_summaries() if manager is not None else []

        return BrowserSessionStatusData(
            session_exists=True,
            browser_running=browser_running,
            lifecycle_state=lifecycle_state,
            active_connections=viewer_count,
            video_streaming=viewer_count > 0,
            viewer_count=viewer_count,
            viewers=viewers,
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
            in_launch_queue=queue_status.in_queue,
            queue_state=queue_status.state,
            queue_type=queue_status.queue_type,
            queue_position=queue_status.position,
            queue_waiting_seconds=queue_status.waiting_seconds,
        )

    async def ensure_webrtc_session(
        self,
        mid: int,
        browser_id: int,
        headless: bool = False,
        is_vip: bool = False,
    ) -> BrowserSessionEntry:
        """
        获取或创建带 WebRTC 能力的浏览器会话。

        WebRTC 管理器在会话创建时已自动初始化，无需额外 enable 调用。

        - 如果会话已存在：直接返回
        - 如果会话不存在：创建新会话（WebRTC 自动可用）

        Args:
            mid: 用户 ID
            browser_id: 浏览器指纹 ID
            headless: 是否无头模式
            is_vip: 是否大会员（决定内存不足时进入 VIP / 普通启动队列）

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
