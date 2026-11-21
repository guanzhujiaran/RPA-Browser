"""会话回收 / 启动队列的卡死自愈测试。

覆盖：
1. ``release_browser_session`` 在优雅关闭卡死时按整体超时返回，并强制清理
   entry / 池对象 / 排队凭证（kill 进程由外部函数负责，这里只断言调用）。
2. ``state=terminating`` 连续回收失败会累计失败次数，达到上限后强制清理。
3. ``launch_queue`` 对「已放行但超过 max_wait 未 launch_settled」的凭证回收
   ``_inflight``，并让等待方失败。
"""

import asyncio
import importlib
import time
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models.runtime.control import BrowserCleanupPolicy, SessionLifecycleState
from app.models.runtime.launch_queue import LaunchQueueStateEnum
from app.models.runtime.live_service import BrowserSessionEntry
from app.models.common.exceptions.base_exception import (
    BrowserLaunchQueueTimeoutException,
)
from app.services.RPA_browser.session.launch_queue import (
    BrowserLaunchQueue,
    LaunchTicket,
)

# 注意：包 __init__ 里的 live_service 是单例实例，会遮蔽同名子模块；
# 这里必须用 importlib 拿到真正的模块，才能 monkeypatch 模块级函数。
live_module = importlib.import_module(
    "app.services.RPA_browser.session.live_service"
)


class _HangingSession:
    """close() 永不返回，模拟 close_all_streams / browser_context.close 卡死"""

    def __init__(self) -> None:
        self.playwright_instance = SimpleNamespace(
            mid=1,
            browser_id=2,
            user_data_dir="/tmp/rpa-test-user-data",
        )
        self.webrtc_manager = None

    async def close(self) -> None:
        await asyncio.sleep(3600)


class _FakePool:
    def __init__(self) -> None:
        self.discarded: list[tuple[int, int]] = []

    async def discard_session(self, mid: int, browser_id: int) -> bool:
        self.discarded.append((mid, browser_id))
        return True

    async def release_session(self, params) -> None:  # pragma: no cover - 超时前不会走到
        return None


class _FakeQueue:
    def __init__(self) -> None:
        self.aborted: list[tuple[int, int]] = []

    async def cancel(self, mid: int, browser_id: int) -> bool:
        # 模拟「已放行」的凭证：cancel 无法取消，需要 abort_launch
        return False

    async def abort_launch(
        self, mid: int, browser_id: int, *, reason: str = ""
    ) -> bool:
        self.aborted.append((mid, browser_id))
        return True

    async def notify_capacity_freed(self) -> None:
        return None

    async def launch_settled(self, mid: int, browser_id: int) -> None:
        return None


class _StaleLaunchQueue:
    """模拟「已放行但迟迟未落地」的队列：第二次 create 必须快速失败。"""

    def __init__(self, launching_seconds: int) -> None:
        self.ticket = LaunchTicket(
            mid=1,
            browser_id=2,
            is_vip=False,
            enqueued_at=time.monotonic(),
        )
        self.ticket.admitted = True
        self.ticket.admitted_at = time.monotonic()
        self._launching_seconds = launching_seconds

    def has_pending(self, mid: int, browser_id: int) -> bool:
        return True

    def reserve(self, mid: int, browser_id: int, *, is_vip: bool) -> LaunchTicket:
        return self.ticket

    def get_launching_seconds(self, mid: int, browser_id: int) -> int:
        return self._launching_seconds

    def get_entry_status(self, mid: int, browser_id: int):
        return SimpleNamespace(queue_type=None, position=None)


@pytest.mark.asyncio
async def test_release_timeout_force_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    service = live_module.LiveService()
    service._browser_sessions.clear()
    entry = BrowserSessionEntry(
        mid=1, browser_id=2, browser_session=_HangingSession()
    )
    service._browser_sessions["1_2"] = entry

    fake_pool = _FakePool()
    fake_queue = _FakeQueue()
    killed: list[str] = []

    monkeypatch.setattr(settings, "browser_session_release_timeout", 1)
    monkeypatch.setattr(live_module, "get_default_session_pool", lambda: fake_pool)
    monkeypatch.setattr(live_module, "get_launch_queue", lambda: fake_queue)
    monkeypatch.setattr(
        live_module,
        "kill_session_processes",
        lambda user_data_dir: killed.append(user_data_dir) or 3,
    )

    ok = await service.release_browser_session(1, 2)

    assert ok is True
    assert "1_2" not in service._browser_sessions
    assert killed == ["/tmp/rpa-test-user-data"]
    assert fake_pool.discarded == [(1, 2)]
    # 释放入口与强制清理都会结算凭证（幂等），至少发生过一次
    assert (1, 2) in fake_queue.aborted

    service._browser_sessions.clear()


@pytest.mark.asyncio
async def test_terminating_retry_then_force_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = live_module.LiveService()
    service._browser_sessions.clear()
    now = int(time.time())
    entry = BrowserSessionEntry(
        mid=3,
        browser_id=4,
        browser_session=_HangingSession(),
        last_activity=now - 1000,
    )
    entry.cleanup_policy = BrowserCleanupPolicy(
        max_idle_time=10, cleanup_interval=60
    )
    entry.terminate_scheduled_at = now - 1000
    entry.lifecycle_state = SessionLifecycleState.TERMINATING
    service._browser_sessions["3_4"] = entry

    monkeypatch.setattr(settings, "browser_session_terminate_grace", 0)
    monkeypatch.setattr(settings, "browser_session_terminate_max_retries", 2)

    async def _release_failed(mid: int, browser_id: int) -> bool:
        return False

    forced: list[str] = []

    async def _force(mid: int, browser_id: int, session_key: str, *, reason: str) -> bool:
        forced.append(session_key)
        service._browser_sessions.pop(session_key, None)
        return True

    monkeypatch.setattr(service, "release_browser_session", _release_failed)
    monkeypatch.setattr(service, "_force_cleanup_browser_session", _force)

    await service._check_session_cleanup()
    assert entry.cleanup_failures == 1
    assert forced == []

    await service._check_session_cleanup()
    assert forced == ["3_4"]
    assert "3_4" not in service._browser_sessions

    service._browser_sessions.clear()


def _admitted_ticket(
    *, admitted_seconds_ago: float, mid: int = 5, browser_id: int = 6
) -> LaunchTicket:
    ticket = LaunchTicket(
        mid=mid,
        browser_id=browser_id,
        is_vip=False,
        enqueued_at=time.monotonic() - admitted_seconds_ago,
    )
    ticket.admitted = True
    ticket.admitted_at = time.monotonic() - admitted_seconds_ago
    return ticket


def test_launch_queue_reaps_stale_launching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "browser_launch_queue_max_wait_time", 10)
    queue = BrowserLaunchQueue()
    ticket = _admitted_ticket(admitted_seconds_ago=100)
    queue._pending[(5, 6)] = ticket
    queue._inflight = 1

    assert queue.get_entry_status(5, 6).state == LaunchQueueStateEnum.LAUNCHING

    reaped = queue._reap_stale_launching_locked()

    assert reaped == 1
    assert queue._inflight == 0
    assert queue.has_pending(5, 6) is False
    assert queue.get_entry_status(5, 6).in_queue is False
    assert ticket.failed is True


@pytest.mark.asyncio
async def test_abort_launch_releases_inflight() -> None:
    queue = BrowserLaunchQueue()
    ticket = LaunchTicket(
        mid=5, browser_id=6, is_vip=False, enqueued_at=time.monotonic()
    )
    queue._pending[(5, 6)] = ticket

    # 先让后台任务进入 wait（模拟真实排队），再放行并强制结算
    waiter = asyncio.create_task(queue.wait(ticket))
    await asyncio.sleep(0.01)
    ticket.admitted = True
    ticket.admitted_at = time.monotonic()
    queue._inflight = 1

    assert await queue.abort_launch(5, 6, reason="test") is True
    with pytest.raises(BrowserLaunchQueueTimeoutException):
        await waiter

    assert queue._inflight == 0
    assert queue.has_pending(5, 6) is False
    assert ticket.failed is True


@pytest.mark.asyncio
async def test_repeated_launch_cycles_leave_no_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续 3 次启停后 launching 必须回到 0（无 _inflight 泄漏）。"""
    monkeypatch.setattr(settings, "browser_launch_queue_enabled", True)
    queue = BrowserLaunchQueue()
    monkeypatch.setattr(queue, "_admission_budget", lambda: 1)
    monkeypatch.setattr(queue, "_cooldown_remaining", lambda: 0.0)

    for _ in range(3):
        ticket = queue.reserve(7, 8, is_vip=False)
        assert ticket.admitted is True
        assert queue.get_status().launching == 1
        await queue.launch_settled(7, 8)
        assert queue.get_status().launching == 0

    assert queue.has_pending(7, 8) is False


@pytest.mark.asyncio
async def test_create_fails_fast_when_launching_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已放行超过 settle_timeout 仍未落地：create 必须快速失败并强制清理。"""
    service = live_module.LiveService()
    service._browser_sessions.clear()
    queue = _StaleLaunchQueue(launching_seconds=100)

    monkeypatch.setattr(live_module, "get_launch_queue", lambda: queue)
    monkeypatch.setattr(settings, "browser_launch_settle_timeout", 30)

    forced: list[str] = []

    async def _force(mid: int, browser_id: int, session_key: str, *, reason: str) -> bool:
        forced.append(session_key)
        return True

    monkeypatch.setattr(service, "_force_cleanup_browser_session", _force)

    result = await live_module.LiveService.create_browser_session(service, 1, 2)

    assert result.success is False
    assert "超时" in (result.error or "")
    assert forced == ["1_2"]
