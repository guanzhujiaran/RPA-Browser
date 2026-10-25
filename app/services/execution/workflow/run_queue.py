"""同浏览器工作流运行队列（进程内 FIFO 串行）。

设计见 ``docs/同浏览器工作流排队计划书.md``：

- 粒度 ``(mid, browser_id)``：同一浏览器同一时刻只跑一个工作流；
- 队列是**内存对象**，重启即清空 —— 排队的「意图」由 ``WorkflowRunRecord``
  的 ``status=QUEUED`` 承载，启动时由 ``requeue_pending_runs()`` 重新入队；
- 唤醒用 ``asyncio.Event``（即时），外加 2s 兜底轮询，避免事件丢失导致永久挂起。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from loguru import logger

#: 队列键：(mid, browser_id)
QueueKey = tuple[int, int]

#: 兜底轮询间隔（秒）：事件丢失时仍能推进
_POLL_INTERVAL_SECONDS = 2.0


class QueueTimeoutError(RuntimeError):
    """排队超时（等待执行权超过配置上限）。"""


@dataclass(slots=True)
class QueueEntry:
    """一个排队中的运行。"""

    run_id: str
    workflow_id: int
    mid: int
    browser_id: int
    enqueued_at: float = field(default_factory=time.monotonic)


class BrowserRunQueue:
    """按浏览器串行执行的内存队列。"""

    def __init__(self) -> None:
        self._pending: dict[QueueKey, deque[str]] = {}
        self._active: dict[QueueKey, str] = {}
        self._events: dict[QueueKey, asyncio.Event] = {}
        self._entries: dict[str, QueueEntry] = {}

    # ── 入队 / 等待 ───────────────────────────────────

    def enqueue(self, entry: QueueEntry) -> int:
        """把运行放入队列（同步），返回当前位置（1 基）。

        与 :meth:`wait_turn` 分开：``launch_workflow_run`` 需要**立即**知道
        排队位置返回给调用方，而真正的执行要等后台 task 里 ``wait_turn``。
        """
        key: QueueKey = (entry.mid, entry.browser_id)
        queue = self._pending.setdefault(key, deque())
        if entry.run_id not in queue:
            queue.append(entry.run_id)
            logger.info(
                f"[RunQueue] 入队: run={entry.run_id}, browser={entry.browser_id}, "
                f"workflow={entry.workflow_id}, position={len(queue)}"
            )
        self._entries[entry.run_id] = entry
        return queue.index(entry.run_id) + 1

    async def wait_turn(self, entry: QueueEntry, *, timeout_seconds: int) -> int:
        """等待执行权（未入队时先自动入队），返回入队位置。

        Raises:
            QueueTimeoutError: 等待超过 ``timeout_seconds``（已自行出队）。
        """
        key: QueueKey = (entry.mid, entry.browser_id)
        position = self.enqueue(entry)
        try:
            await asyncio.wait_for(
                self._wait_turn(key, entry.run_id), timeout=timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            self.cancel(key, entry.run_id)
            raise QueueTimeoutError(
                f"排队等待超过 {timeout_seconds}s 仍未获得浏览器执行权"
            ) from exc
        return position

    async def acquire(self, entry: QueueEntry, *, timeout_seconds: int) -> int:
        """入队并等待执行权（``enqueue`` + ``wait_turn`` 的组合，供测试/直接调用）。"""
        return await self.wait_turn(entry, timeout_seconds=timeout_seconds)

    async def _wait_turn(self, key: QueueKey, run_id: str) -> None:
        """等到自己成为队首且无 active 运行。"""
        while True:
            queue = self._pending.get(key)
            if queue and queue[0] == run_id and key not in self._active:
                queue.popleft()
                self._active[key] = run_id
                logger.info(f"[RunQueue] 获得执行权: run={run_id}, browser={key[1]}")
                return

            event = self._events.setdefault(key, asyncio.Event())
            event.clear()
            try:
                await asyncio.wait_for(
                    event.wait(), timeout=_POLL_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                continue

    def release(self, key: QueueKey, run_id: str) -> None:
        """释放执行权并唤醒下一位（幂等）。

        同时兜底清理 pending：task 在 ``wait_turn`` 之前被取消（或异常退出）时，
        该 run 可能仍在队列里，若不清掉会永久占住队首，导致后序运行全部饿死。
        """
        if self._active.get(key) == run_id:
            self._active.pop(key, None)
        queue = self._pending.get(key)
        if queue and run_id in queue:
            queue.remove(run_id)
        self._entries.pop(run_id, None)
        self._wake(key)

    # ── 查询 / 取消 ───────────────────────────────────

    def position(self, key: QueueKey, run_id: str) -> int:
        """排队位置（1 基）；正在执行或不在队列时返回 0。"""
        queue = self._pending.get(key)
        if not queue or run_id not in queue:
            return 0
        return queue.index(run_id) + 1

    def is_active(self, key: QueueKey, run_id: str) -> bool:
        """该运行是否正持有执行权。"""
        return self._active.get(key) == run_id

    def pending_count(self, key: QueueKey) -> int:
        """该浏览器排队中的数量（不含正在执行的）。"""
        return len(self._pending.get(key) or ())

    def has_pending(self, key: QueueKey) -> bool:
        """该浏览器是否还有排队项（决定运行结束后是否回收浏览器）。"""
        return bool(self._pending.get(key))

    def is_busy(self, key: QueueKey) -> bool:
        """该浏览器是否仍有排队项或正在执行的运行（决定是否保留会话）。"""
        return bool(self._pending.get(key)) or key in self._active

    def cancel(self, key: QueueKey, run_id: str) -> bool:
        """从排队中移除；正在执行时返回 False（需走运行停止流程）。"""
        queue = self._pending.get(key)
        if not queue or run_id not in queue:
            return False
        queue.remove(run_id)
        self._entries.pop(run_id, None)
        logger.info(f"[RunQueue] 取消排队: run={run_id}, browser={key[1]}")
        self._wake(key)
        return True

    def entry_of(self, run_id: str) -> QueueEntry | None:
        """取排队项元数据（用于恢复/日志）。"""
        return self._entries.get(run_id)

    def snapshot(self) -> dict[str, object]:
        """当前队列快照（自检 / 排障用）。"""
        return {
            "active": {f"{mid}_{bid}": rid for (mid, bid), rid in self._active.items()},
            "pending": {
                f"{mid}_{bid}": list(queue)
                for (mid, bid), queue in self._pending.items()
                if queue
            },
        }

    def reset(self) -> None:
        """清空队列（仅测试使用）。"""
        self._pending.clear()
        self._active.clear()
        self._events.clear()
        self._entries.clear()

    # ── 内部 ─────────────────────────────────────────

    def _wake(self, key: QueueKey) -> None:
        """唤醒该浏览器的所有等待者（由队首自己抢到执行权）。"""
        event = self._events.get(key)
        if event is not None:
            event.set()


#: 全局单例（进程内唯一队列）
run_queue = BrowserRunQueue()

__all__ = [
    "BrowserRunQueue",
    "QueueEntry",
    "QueueKey",
    "QueueTimeoutError",
    "run_queue",
]
