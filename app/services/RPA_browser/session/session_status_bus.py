"""会话状态事件总线（SSE 推送源）。

RPA 服务单进程单 worker 运行（main.py 的 uvicorn.run 未指定 workers），
会话表与订阅者表都落在同一进程内存中，因此用进程内订阅者表即可完成广播，
无需 Redis / MQ。若未来改为多 worker / 多副本，只需在本模块内部替换为跨进程广播。

事件载荷是 `BrowserSessionStatusData` 全量快照：前端断线重连后以首帧为准，
不依赖历史回放，因此不实现 `Last-Event-ID` 续传。

详见 docs/rpa-会话状态SSE推送计划书.md。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from loguru import logger

from app.models.runtime.control import BrowserSessionStatusData

type _SessionKey = tuple[int, int]

#: SSE 事件名（前端据此过滤事件）
SSE_EVENT_NAME = "session_status"
#: 心跳注释行：不产生 data，不会触发前端事件回调
SSE_HEARTBEAT_COMMENT = ": keep-alive"
#: 单连接队列上限：慢消费者只保留最新若干帧
_QUEUE_MAX_SIZE = 32


def format_sse_frame(event: str, data: str) -> str:
    """按 SSE 规范拼帧：`event:` + `data:` + 空行结尾。"""
    return f"event: {event}\ndata: {data}\n\n"


class SessionStatusBus:
    """进程内会话状态广播。"""

    def __init__(self) -> None:
        self._subscribers: dict[
            _SessionKey, set[asyncio.Queue[BrowserSessionStatusData]]
        ] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定事件循环（lifespan 调用），供非循环线程发布时安全投递。"""
        self._loop = loop

    def subscribe(
        self, mid: int, browser_id: int
    ) -> asyncio.Queue[BrowserSessionStatusData]:
        """注册一个订阅者（一条 SSE 连接对应一个队列）。"""
        queue: asyncio.Queue[BrowserSessionStatusData] = asyncio.Queue(
            maxsize=_QUEUE_MAX_SIZE
        )
        self._subscribers.setdefault((mid, browser_id), set()).add(queue)
        return queue

    def unsubscribe(
        self,
        mid: int,
        browser_id: int,
        queue: asyncio.Queue[BrowserSessionStatusData],
    ) -> None:
        """注销订阅者（连接关闭时调用）。"""
        key = (mid, browser_id)
        queues = self._subscribers.get(key)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            self._subscribers.pop(key, None)

    def publish(
        self, mid: int, browser_id: int, status: BrowserSessionStatusData
    ) -> None:
        """发布状态快照。

        在事件循环线程内直接投递；若从其他线程调用，交由已绑定的循环投递。
        """
        if not self.has_subscribers(mid, browser_id):
            return
        key = (mid, browser_id)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is None:
                logger.debug(f"会话状态事件无可用事件循环，已丢弃: {key}")
                return
            self._loop.call_soon_threadsafe(self._publish_now, key, status)
            return
        self._publish_now(key, status)

    def has_subscribers(self, mid: int, browser_id: int) -> bool:
        """该会话是否有订阅者（无订阅者时不构造快照，省去无谓开销）。"""
        return (mid, browser_id) in self._subscribers

    def subscriber_count(self, mid: int, browser_id: int) -> int:
        """该会话当前的 SSE 连接数（供连接/断开日志使用）。"""
        return len(self._subscribers.get((mid, browser_id), ()))

    def _publish_now(self, key: _SessionKey, status: BrowserSessionStatusData) -> None:
        queues = self._subscribers.get(key)
        if not queues:
            return
        for queue in list(queues):
            self._offer(queue, status)

    @staticmethod
    def _offer(
        queue: asyncio.Queue[BrowserSessionStatusData],
        status: BrowserSessionStatusData,
    ) -> None:
        """投递一帧；队列满则丢最旧帧，保证订阅者最终看到最新状态。"""
        try:
            queue.put_nowait(status)
            return
        except asyncio.QueueFull:
            pass
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(status)
        except asyncio.QueueFull:
            logger.warning("会话状态推送队列持续拥塞，已丢弃最新帧")


async def iter_session_status_events(
    mid: int,
    browser_id: int,
    initial: BrowserSessionStatusData,
    heartbeat_interval: float,
) -> AsyncIterator[str]:
    """产出 SSE 文本帧：首帧全量快照 + 变更推送 + 心跳。

    首帧下发当前快照，前端无需在建连后再补一次 `/status` 请求。
    客户端断开时生成器被关闭，`finally` 中反订阅，订阅者表不会残留。
    """
    queue = session_status_bus.subscribe(mid, browser_id)
    # 连接/断开必须留痕：前端「有没有真的连上」在后端此前完全不可观测
    logger.info(
        f"SSE 已连接: mid={mid} browser_id={browser_id} "
        f"当前连接数={session_status_bus.subscriber_count(mid, browser_id)}"
    )
    try:
        yield format_sse_frame(SSE_EVENT_NAME, initial.model_dump_json())
        while True:
            try:
                status = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            except TimeoutError:
                # 心跳仅用于穿过网关空闲超时，不承载业务数据
                yield f"{SSE_HEARTBEAT_COMMENT}\n\n"
                continue
            yield format_sse_frame(SSE_EVENT_NAME, status.model_dump_json())
    finally:
        session_status_bus.unsubscribe(mid, browser_id, queue)
        logger.info(
            f"SSE 已断开: mid={mid} browser_id={browser_id} "
            f"剩余连接数={session_status_bus.subscriber_count(mid, browser_id)}"
        )


session_status_bus = SessionStatusBus()

__all__ = [
    "SSE_EVENT_NAME",
    "SSE_HEARTBEAT_COMMENT",
    "SessionStatusBus",
    "format_sse_frame",
    "iter_session_status_events",
    "session_status_bus",
]
