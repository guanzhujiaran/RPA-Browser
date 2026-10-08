"""浏览器启动队列 - 基于系统内存的准入控制

启动浏览器前先读取系统可用内存：

- 内存充足 → 立即放行；
- 内存不足 → 进入排队等待，队列分两条：**VIP 队列优先于普通用户队列**。

实现要点：

1. **预留记账（reservation）**：请求放行后、浏览器进程内存真正爬升之前，
   先按 ``browser_launch_reserved_memory_mb`` 记账，避免并发放行把内存打爆。
2. **放行冷却（cooldown）**：每次放行/落地后短时间内不再放行，
   给浏览器进程留出内存爬升时间。
3. **兜底轮询**：后台任务周期性重试准入，覆盖「内存被外部进程释放」等场景。

单事件循环模型：所有队列读写在同步代码段内完成（不夹杂 ``await``），
因此状态查询方法可以不加锁直接读取。
"""

import asyncio
import contextlib
import time
from collections import deque
from dataclasses import dataclass, field

from loguru import logger

from app.config import settings
from app.models.common.exceptions.base_exception import (
    BrowserLaunchQueueCancelledException,
    BrowserLaunchQueueTimeoutException,
)
from app.models.runtime.launch_queue import (
    BrowserLaunchQueueWaitingItem,
    LaunchQueueEntryStatus,
    LaunchQueueStateEnum,
    LaunchQueueStatus,
    LaunchQueueTypeEnum,
)
from app.services.RPA_browser.monitor.browser_memory_estimator import (
    browser_memory_estimator,
)
from app.services.RPA_browser.monitor.memory_monitor import memory_monitor

# 会话键：(mid, browser_id)
SessionKey = tuple[int, int]


@dataclass
class LaunchTicket:
    """一次浏览器启动的排队凭证"""

    mid: int
    browser_id: int
    is_vip: bool
    enqueued_at: float
    event: asyncio.Event = field(default_factory=asyncio.Event)
    admitted: bool = False
    cancelled: bool = False

    @property
    def session_key(self) -> SessionKey:
        return (self.mid, self.browser_id)

    @property
    def queue_type(self) -> LaunchQueueTypeEnum:
        return LaunchQueueTypeEnum.VIP if self.is_vip else LaunchQueueTypeEnum.NORMAL

    @property
    def waiting_seconds(self) -> int:
        return int(time.monotonic() - self.enqueued_at)


class BrowserLaunchQueue:
    """浏览器启动队列（内存准入 + VIP/普通双队列）"""

    def __init__(self) -> None:
        self._vip_queue: deque[LaunchTicket] = deque()
        self._normal_queue: deque[LaunchTicket] = deque()
        # 已受理的启动凭证（排队中 + 启动中），key=(mid, browser_id)
        self._pending: dict[SessionKey, LaunchTicket] = {}
        self._lock = asyncio.Lock()
        # 已放行但尚未「落地」的启动数（内存预留记账）
        self._inflight = 0
        # 最近一次放行/落地的时刻（用于冷却判断）
        self._last_admit_at = 0.0
        # ETA 采样：仅在「放行后队列仍有等待者」时记录放行时刻——该间隔由内存释放 +
        # 冷却共同决定，才是排队时长的有效信号（见 _average_admit_interval）
        self._admit_stamps: deque[float] = deque(
            maxlen=max(2, settings.browser_launch_eta_sample_window)
        )
        self._submitted_total = 0
        self._queued_total = 0
        self._timeout_total = 0
        self._ticker: asyncio.Task | None = None
        self._scan_task: asyncio.Task | None = None

    # ────────────────────────── 生命周期 ──────────────────────────

    def start(self) -> None:
        """启动后台兜底轮询（幂等）"""
        if not settings.browser_launch_queue_enabled:
            logger.info("浏览器启动队列未启用，跳过后台轮询")
            return
        if self._ticker is None or self._ticker.done():
            # 启动即采样一次，避免前 15 秒用不到实测额度
            self._schedule_memory_scan(0)
            self._ticker = asyncio.create_task(self._tick_loop())
            logger.info("浏览器启动队列后台轮询已启动")

    async def stop(self) -> None:
        """停止后台兜底轮询"""
        for task in (self._ticker, self._scan_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._ticker = None
        self._scan_task = None

    async def _tick_loop(self) -> None:
        interval = max(0.1, settings.browser_launch_queue_tick_interval_ms / 1000)
        while True:
            await asyncio.sleep(interval)
            try:
                # 定期做一次全量实测（扫描进程较慢，放线程池），用真实占用校准预留额度
                if (
                    browser_memory_estimator.seconds_since_scan()
                    >= settings.browser_memory_scan_interval
                ):
                    self._schedule_memory_scan(0)
                async with self._lock:
                    self._try_admit_locked()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"浏览器启动队列轮询失败: {e}")

    def _schedule_memory_scan(self, delay: float) -> None:
        """安排一次浏览器内存实测（已有待执行/进行中的扫描则复用之）"""
        if not settings.browser_memory_estimate_enabled:
            return
        if self._scan_task is not None and not self._scan_task.done():
            return
        self._scan_task = asyncio.create_task(self._delayed_memory_scan(delay))

    async def _delayed_memory_scan(self, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(browser_memory_estimator.refresh)
            async with self._lock:
                self._try_admit_locked()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"浏览器内存实测失败: {e}")

    # ────────────────────────── 准入判断 ──────────────────────────

    def _admission_budget(self) -> int:
        """当前还能放行多少个启动请求

        三道约束同时生效：

        1. **系统内存使用率红线**：``available`` 在 Linux 上会把可回收的 page cache
           算进来，偏乐观，单靠它容易触发 OOM Killer；超过红线一律不放行。
        2. **实测内存预留记账**：已放行但尚未落地的实例按实测推算出的单实例额度
           （``browser_memory_estimator.reserved_mb()``）预先扣减，避免并发放行打爆内存。
           已落地实例的真实占用本身就体现在 ``available`` 里，无需重复扣减。
        3. **并发实例数上限**：运行中（实测扫描到的）+ 启动中的实例数不得超过配置上限。
        """
        snapshot = memory_monitor.snapshot()

        # 约束 1：系统内存使用率红线
        if snapshot.used_percent >= settings.browser_launch_max_memory_percent:
            return 0

        # 约束 2：实测预留额度
        reserved_per_instance = max(1, browser_memory_estimator.reserved_mb())
        usable = snapshot.available_mb - self._inflight * reserved_per_instance
        if usable < settings.browser_launch_min_available_memory_mb:
            return 0
        budget = int(usable // reserved_per_instance)

        # 约束 3：并发实例数上限
        max_instances = settings.browser_max_concurrent_instances
        if max_instances > 0:
            occupied = browser_memory_estimator.active_instances() + self._inflight
            budget = min(budget, max(0, max_instances - occupied))

        return max(0, budget)

    def _cooldown_remaining(self) -> float:
        """距离下次允许放行还需等待的秒数"""
        cooldown = settings.browser_launch_admit_cooldown_ms / 1000
        if cooldown <= 0:
            return 0.0
        return max(0.0, cooldown - (time.monotonic() - self._last_admit_at))

    def would_queue(self, mid: int, browser_id: int, *, is_vip: bool) -> bool:
        """预判该启动请求是否需要排队（供路由层决定是否立即返回「排队中」）"""
        if not settings.browser_launch_queue_enabled:
            return False
        if (mid, browser_id) in self._pending:
            return True
        # VIP 只被 VIP 队列阻塞；普通用户还需排在 VIP 之后
        if self._vip_queue:
            return True
        if self._normal_queue and not is_vip:
            return True
        return self._admission_budget() <= 0

    # ────────────────────────── 取号 / 放行 ──────────────────────────

    def reserve(self, mid: int, browser_id: int, *, is_vip: bool) -> LaunchTicket:
        """同步取号（不阻塞）：创建/复用排队凭证，并尝试立即放行。

        调用方拿到凭证后自行决定是「直接启动」还是「等待放行」。
        具备幂等性：同一会话重复取号返回同一凭证。
        """
        key: SessionKey = (mid, browser_id)
        ticket = self._pending.get(key)
        if ticket is not None:
            return ticket

        ticket = LaunchTicket(
            mid=mid,
            browser_id=browser_id,
            is_vip=is_vip,
            enqueued_at=time.monotonic(),
        )
        self._pending[key] = ticket
        self._submitted_total += 1
        if is_vip:
            self._vip_queue.append(ticket)
        else:
            self._normal_queue.append(ticket)
        self._try_admit_locked()

        if not ticket.admitted:
            self._queued_total += 1
            logger.info(
                f"浏览器启动进入排队: mid={mid}, browser_id={browser_id}, "
                f"队列={ticket.queue_type.value}, 可用内存={memory_monitor.available_mb()}MB"
            )
        return ticket

    async def acquire(
        self,
        mid: int,
        browser_id: int,
        *,
        is_vip: bool,
        timeout: float | None = None,
    ) -> None:
        """申请浏览器启动名额。

        - 内存允许时立即返回；
        - 否则进入队列等待，直到被放行、超时或被取消。

        Raises:
            BrowserLaunchQueueTimeoutException: 排队超过最大等待时间。
            BrowserLaunchQueueCancelledException: 排队过程中被主动取消。
        """
        if not settings.browser_launch_queue_enabled:
            return

        ticket = self.reserve(mid, browser_id, is_vip=is_vip)
        if ticket.admitted:
            return
        await self.wait(ticket, timeout=timeout)

    async def wait(self, ticket: LaunchTicket, timeout: float | None = None) -> None:
        """等待凭证被放行（排队阻塞点）

        Raises:
            BrowserLaunchQueueTimeoutException: 排队超过最大等待时间。
            BrowserLaunchQueueCancelledException: 排队过程中被主动取消。
        """
        if ticket.admitted:
            return

        wait_seconds = (
            settings.browser_launch_queue_max_wait_time if timeout is None else timeout
        )

        try:
            if wait_seconds and wait_seconds > 0:
                await asyncio.wait_for(ticket.event.wait(), wait_seconds)
            else:
                await ticket.event.wait()
        except TimeoutError:
            async with self._lock:
                # 等待与放行竞争：已被放行则照常返回，避免凭空占住预留额度
                if ticket.admitted:
                    return
                self._discard_ticket_locked(ticket)
                self._timeout_total += 1
            logger.warning(
                f"浏览器启动排队超时: mid={ticket.mid}, browser_id={ticket.browser_id}, "
                f"等待={wait_seconds}s"
            )
            raise BrowserLaunchQueueTimeoutException(max(1, int(wait_seconds)))
        except asyncio.CancelledError:
            async with self._lock:
                if not ticket.admitted:
                    self._discard_ticket_locked(ticket)
            raise

        if ticket.cancelled:
            raise BrowserLaunchQueueCancelledException()

    async def launch_settled(self, mid: int, browser_id: int) -> None:
        """浏览器启动结束（成功或失败）后调用：释放预留额度并放行下一个"""
        key: SessionKey = (mid, browser_id)
        async with self._lock:
            ticket = self._pending.pop(key, None)
            admitted = ticket is not None and ticket.admitted
            if admitted:
                self._inflight = max(0, self._inflight - 1)
                # 从「落地」时刻重新计时冷却，等浏览器进程内存爬升到真实水平
                self._last_admit_at = time.monotonic()
            self._try_admit_locked()

        if admitted:
            # 延迟实测：等浏览器把页面/内存真正吃起来后再采样一次，样本更贴近稳态
            self._schedule_memory_scan(settings.browser_memory_post_launch_delay)

    async def cancel(self, mid: int, browser_id: int) -> bool:
        """取消某个会话的排队/启动（用户主动关闭）。返回是否确实取消了排队中的请求。"""
        key: SessionKey = (mid, browser_id)
        async with self._lock:
            ticket = self._pending.get(key)
            if ticket is None:
                return False
            if ticket.admitted:
                # 已放行则交给 release_browser_session 正常回收，这里不打断
                return False
            ticket.cancelled = True
            ticket.event.set()
            self._discard_ticket_locked(ticket)
            logger.info(f"浏览器启动排队已取消: mid={mid}, browser_id={browser_id}")
            return True

    async def notify_capacity_freed(self) -> None:
        """浏览器被回收、内存释放后调用，立即尝试放行下一个请求"""
        async with self._lock:
            self._try_admit_locked()

    # ────────────────────────── 内部实现（持锁） ──────────────────────────

    def _try_admit_locked(self) -> None:
        """尝试放行队首请求（VIP 优先）。

        未启用队列时，所有等待者直接放行（等同不做限制）。
        """
        if not settings.browser_launch_queue_enabled:
            while (ticket := self._pop_next_locked()) is not None:
                self._admit_locked(ticket)
            return

        while True:
            if self._admission_budget() <= 0 or self._cooldown_remaining() > 0:
                break
            ticket = self._pop_next_locked()
            if ticket is None:
                break
            self._admit_locked(ticket)

    def _pop_next_locked(self) -> LaunchTicket | None:
        """取出下一个待放行凭证：VIP 队列优先，其次普通队列"""
        for queue in (self._vip_queue, self._normal_queue):
            while queue:
                ticket = queue.popleft()
                if not ticket.cancelled:
                    return ticket
        return None

    def _admit_locked(self, ticket: LaunchTicket) -> None:
        ticket.admitted = True
        self._inflight += 1
        now = time.monotonic()
        self._last_admit_at = now
        # ETA 采样：放行后队列仍有等待者 → 本次放行属于「连续消化」，间隔可用；
        # 队列已排空则跳过，避免把空闲时长误当成排队节奏
        if self._vip_queue or self._normal_queue:
            self._admit_stamps.append(now)
        ticket.event.set()
        logger.info(
            f"浏览器启动已放行: mid={ticket.mid}, browser_id={ticket.browser_id}, "
            f"队列={ticket.queue_type.value}, 排队等待={ticket.waiting_seconds}s"
        )

    def _discard_ticket_locked(self, ticket: LaunchTicket) -> None:
        """从队列与受理表中移除凭证"""
        self._pending.pop(ticket.session_key, None)
        for queue in (self._vip_queue, self._normal_queue):
            with contextlib.suppress(ValueError):
                queue.remove(ticket)

    # ────────────────────────── 状态查询 ──────────────────────────

    def get_status(self) -> LaunchQueueStatus:
        """全局队列状态（供监控 / 管理端使用）"""
        return LaunchQueueStatus(
            enabled=settings.browser_launch_queue_enabled,
            vip_waiting=sum(1 for t in self._vip_queue if not t.cancelled),
            normal_waiting=sum(1 for t in self._normal_queue if not t.cancelled),
            launching=self._inflight,
            submitted_total=self._submitted_total,
            queued_total=self._queued_total,
            timeout_total=self._timeout_total,
            memory=memory_monitor.snapshot(),
            browser_memory=browser_memory_estimator.status(),
            max_instances=settings.browser_max_concurrent_instances,
        )

    def get_entry_status(self, mid: int, browser_id: int) -> LaunchQueueEntryStatus:
        """单个会话的排队状态"""
        ticket = self._pending.get((mid, browser_id))
        if ticket is None:
            return LaunchQueueEntryStatus()

        if ticket.admitted:
            # 已放行：剩余时长由浏览器启动耗时决定，不再受队列影响
            return LaunchQueueEntryStatus(
                in_queue=True,
                state=LaunchQueueStateEnum.LAUNCHING,
                queue_type=ticket.queue_type,
                position=None,
                waiting_seconds=ticket.waiting_seconds,
                estimated_wait_seconds=0,
                estimate_reliable=True,
            )

        estimated, reliable = self._estimate_wait(ticket)
        return LaunchQueueEntryStatus(
            in_queue=True,
            state=LaunchQueueStateEnum.QUEUED,
            queue_type=ticket.queue_type,
            position=self._position_of(ticket),
            waiting_seconds=ticket.waiting_seconds,
            estimated_wait_seconds=estimated,
            estimate_reliable=reliable,
        )

    def _position_of(self, ticket: LaunchTicket) -> int | None:
        """凭证在自身队列中的位置（1 起）"""
        queue = self._vip_queue if ticket.is_vip else self._normal_queue
        try:
            return queue.index(ticket) + 1
        except ValueError:
            return None

    # ────────────────────────── 排队时长估算（ETA） ──────────────────────────

    def _average_admit_interval(self) -> float | None:
        """实测「连续放行间隔」均值(秒)；样本不足返回 None

        间隔 = 相邻两次放行的时间差，反映「内存释放 + 放行冷却」的综合节奏：
        内存越紧张，实测间隔越大，估算随之自动放大——这是本估算能自校准的关键。
        """
        stamps = list(self._admit_stamps)
        if len(stamps) < max(2, settings.browser_launch_eta_min_samples):
            return None
        gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
        return sum(gaps) / len(gaps)

    def _ahead_count(self, ticket: LaunchTicket) -> int:
        """凭证前方的等待者数量

        VIP 队列优先，因此普通用户还需把 VIP 队列全部算在身前。
        """
        vip = [t for t in self._vip_queue if not t.cancelled]
        normal = [t for t in self._normal_queue if not t.cancelled]
        if ticket.is_vip:
            return vip.index(ticket) if ticket in vip else 0
        return len(vip) + (normal.index(ticket) if ticket in normal else 0)

    def _estimate_wait(self, ticket: LaunchTicket) -> tuple[int, bool]:
        """估算凭证还需等待的秒数与可信度

        按「排队慢在哪」分两种情形，因为主导因素不同：

        - **名额已释放**（``_admission_budget() > 0``）：剩下的等待只来自放行冷却，
          估算 = 当前冷却剩余 + 前方人数 × 冷却周期，**可信**；
        - **名额不足**：时间主要花在「等别的会话释放内存」上，用实测连续放行间隔
          推算——内存越紧张、实测间隔越大，估算随之自动放大。但「何时释放」取决于
          其他会话的关闭时机，无法预测，故标记**不可信**；样本不足时退化为冷却下限
          （只反映连续放行节奏、不含内存等待，必然偏乐观）。

        注意队首（前方 0 人）在名额不足时并非「立刻可启动」，仍要等下一次放行，
        因此按 ``(前方人数 + 1)`` 个间隔估算，避免给出 0 秒的误导值。
        """
        ahead = self._ahead_count(ticket)
        cooldown = max(0.0, settings.browser_launch_admit_cooldown_ms / 1000)

        if self._admission_budget() > 0:
            return int(self._cooldown_remaining() + ahead * cooldown), True

        interval = self._average_admit_interval()
        if interval is None:
            return int(ahead * cooldown), False
        return int((ahead + 1) * interval), False

    def list_waiting_items(self) -> list[BrowserLaunchQueueWaitingItem]:
        """列出排队 / 启动中的会话明细（管理端监管用）

        排序：VIP 队列优先，其次按已等待时长降序（等得久的排前面）。
        """
        items = [
            self._to_waiting_item(ticket)
            for ticket in self._pending.values()
            if not ticket.cancelled
        ]
        items.sort(
            key=lambda item: (
                item.queue_type != LaunchQueueTypeEnum.VIP,
                -item.waiting_seconds,
            )
        )
        return items

    def _to_waiting_item(self, ticket: LaunchTicket) -> BrowserLaunchQueueWaitingItem:
        if ticket.admitted:
            position = None
            state = LaunchQueueStateEnum.LAUNCHING
        else:
            position = self._position_of(ticket)
            state = LaunchQueueStateEnum.QUEUED

        return BrowserLaunchQueueWaitingItem(
            mid=ticket.mid,
            mid_str=str(ticket.mid),
            browser_id=ticket.browser_id,
            browser_id_str=str(ticket.browser_id),
            queue_type=ticket.queue_type,
            state=state,
            position=position,
            waiting_seconds=ticket.waiting_seconds,
        )


# 全局单例
_default_launch_queue: BrowserLaunchQueue | None = None


def get_launch_queue() -> BrowserLaunchQueue:
    """获取全局启动队列实例"""
    global _default_launch_queue
    if _default_launch_queue is None:
        _default_launch_queue = BrowserLaunchQueue()
    return _default_launch_queue


__all__ = [
    "BrowserLaunchQueue",
    "LaunchTicket",
    "get_launch_queue",
]
