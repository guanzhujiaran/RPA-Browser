"""同浏览器运行队列单测（纯内存，不依赖 DB / 浏览器）。"""

from __future__ import annotations

import asyncio

import pytest

from app.services.execution.workflow.run_queue import (
    BrowserRunQueue,
    QueueEntry,
    QueueTimeoutError,
)

KEY = (100, 200)  # (mid, browser_id)


@pytest.fixture
def queue() -> BrowserRunQueue:
    """每个用例独立队列。"""
    return BrowserRunQueue()


def _entry(run_id: str, workflow_id: int = 1) -> QueueEntry:
    return QueueEntry(
        run_id=run_id, workflow_id=workflow_id, mid=KEY[0], browser_id=KEY[1]
    )


def test_enqueue_keeps_fifo_positions(queue: BrowserRunQueue) -> None:
    """入队按提交顺序给位置，重复入队不改变位置。"""
    assert queue.enqueue(_entry("r1")) == 1
    assert queue.enqueue(_entry("r2")) == 2
    assert queue.enqueue(_entry("r2")) == 2  # 幂等

    assert queue.position(KEY, "r1") == 1
    assert queue.position(KEY, "r2") == 2
    assert queue.position(KEY, "unknown") == 0
    assert queue.pending_count(KEY) == 2


async def test_wait_turn_is_serial(queue: BrowserRunQueue) -> None:
    """同一浏览器同一时刻只有一个运行持有执行权，释放后下一个立即接手。"""
    first, second = _entry("r1"), _entry("r2")
    queue.enqueue(first)
    queue.enqueue(second)

    task1 = asyncio.create_task(queue.wait_turn(first, timeout_seconds=2))
    await asyncio.sleep(0.05)
    assert queue.is_active(KEY, "r1")

    task2 = asyncio.create_task(queue.wait_turn(second, timeout_seconds=2))
    await asyncio.sleep(0.05)
    assert queue.is_active(KEY, "r2") is False
    assert queue.position(KEY, "r2") == 1  # r1 已出队，r2 排到队首

    queue.release(KEY, "r1")
    await asyncio.wait_for(task2, timeout=1)
    assert queue.is_active(KEY, "r2")
    assert queue.is_busy(KEY) is True

    queue.release(KEY, "r2")
    await asyncio.wait_for(task1, timeout=1)
    assert queue.is_busy(KEY) is False


async def test_cancel_removes_from_queue(queue: BrowserRunQueue) -> None:
    """排队中可以取消；正在执行时不允许通过 cancel 取消。"""
    queue.enqueue(_entry("r1"))
    queue.enqueue(_entry("r2"))

    assert queue.cancel(KEY, "r2") is True
    assert queue.position(KEY, "r2") == 0
    assert queue.cancel(KEY, "r2") is False

    holder = asyncio.create_task(queue.wait_turn(_entry("r1"), timeout_seconds=2))
    await asyncio.sleep(0.05)
    assert queue.is_active(KEY, "r1")
    assert queue.cancel(KEY, "r1") is False  # 执行中不走出队
    queue.release(KEY, "r1")
    await asyncio.wait_for(holder, timeout=1)


async def test_wait_turn_timeout_removes_entry(queue: BrowserRunQueue) -> None:
    """排队超时抛 QueueTimeoutError 并自动出队。"""
    holder_entry = _entry("holder")
    queue.enqueue(holder_entry)
    holder = asyncio.create_task(queue.wait_turn(holder_entry, timeout_seconds=2))
    await asyncio.sleep(0.05)
    assert queue.is_active(KEY, "holder")

    waiter = _entry("waiter")
    with pytest.raises(QueueTimeoutError):
        await queue.wait_turn(waiter, timeout_seconds=0.05)

    assert queue.position(KEY, "waiter") == 0
    queue.release(KEY, "holder")
    await asyncio.wait_for(holder, timeout=1)


def test_different_browsers_are_independent(queue: BrowserRunQueue) -> None:
    """不同浏览器各自排队，互不影响。"""
    other_key = (100, 201)
    queue.enqueue(_entry("r1"))
    other = QueueEntry(run_id="r2", workflow_id=1, mid=100, browser_id=201)
    queue.enqueue(other)

    assert queue.position(KEY, "r1") == 1
    assert queue.position(other_key, "r2") == 1


def test_release_without_active_is_idempotent(queue: BrowserRunQueue) -> None:
    """重复 release / 释放非持有者不会破坏队列。"""
    queue.enqueue(_entry("r1"))
    queue.release(KEY, "not-holder")
    assert queue.position(KEY, "r1") == 1

    queue.release(KEY, "r1")
    assert queue.is_busy(KEY) is False
