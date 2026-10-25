"""notify 内置动作单测（离线，不依赖浏览器 / MQ / 数据库）

覆盖 ``docs/通知动作与运行告警计划书.md`` 的关键语义：
- 双通道投递与通道选择；
- 按天去重（同 key 同一天只发一次，跨天可再发）；
- 通道异常不影响动作结论（弱依赖）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.execution.action_params import NotifyParams
from app.services.execution.actions import notify as notify_mod
from app.services.execution.actions.notify import NotifyAction, reset_dedupe


class _FakeConfig:
    """外部推送配置占位（push_msg.send 只透传）"""


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_dedupe()
    yield
    reset_dedupe()


@pytest.fixture
def channels(monkeypatch):
    """把两个通道替换成可观测的假实现"""
    publish = AsyncMock(return_value=True)
    push = AsyncMock(return_value=None)
    monkeypatch.setattr(notify_mod, "publish_notify", publish)
    monkeypatch.setattr(notify_mod.push_msg, "send", push)

    class _BrowserSvc:
        def __init__(self, mid):
            self.mid = mid

        async def get_notification_config(self, session):
            return _FakeConfig()

    monkeypatch.setattr(notify_mod, "BrowserService", _BrowserSvc)
    return SimpleNamespace(publish=publish, push=push)


async def run_notify(params: NotifyParams):
    action = NotifyAction.new_action(
        mid=1001, page=None, variables={}, params=params
    )
    return await action.execute()


@pytest.mark.asyncio
async def test_both_channels_and_dedupe(channels):
    params = NotifyParams(
        title="LLM Key 失效", content="请更换 key", dedupe_key="llm_key_invalid"
    )

    first = await run_notify(params)
    assert first.success
    assert first.data.inbox_sent and first.data.push_sent
    assert first.data.deduped is False
    assert channels.publish.await_count == 1
    assert channels.push.await_count == 1

    second = await run_notify(params)
    assert second.success
    assert second.data.deduped is True, "同 key 同一天应被去重"
    assert channels.publish.await_count == 1, "去重后不应再投递"


@pytest.mark.asyncio
async def test_channel_inbox_only(channels):
    result = await run_notify(
        NotifyParams(title="仅站内", channel="inbox", once_per_day=False)
    )

    assert result.success
    assert result.data.inbox_sent is True
    assert result.data.push_sent is False
    assert channels.push.await_count == 0


@pytest.mark.asyncio
async def test_push_failure_does_not_break_action(channels):
    channels.publish.side_effect = RuntimeError("rpc down")

    result = await run_notify(NotifyParams(title="站内挂了", content="x"))

    assert result.success, "通知失败不能中断工作流"
    assert result.data.inbox_sent is False
    assert result.data.push_sent is True


@pytest.mark.asyncio
async def test_dedupe_resets_next_day(channels, monkeypatch):
    params = NotifyParams(title="额度耗尽", dedupe_key="llm_quota_exhausted")

    await run_notify(params)
    assert channels.publish.await_count == 1

    # 模拟第二天：去重窗口变化 → 允许再发
    monkeypatch.setattr(notify_mod, "_today", lambda: "2999-01-01")
    again = await run_notify(params)

    assert again.data.deduped is False
    assert channels.publish.await_count == 2


@pytest.mark.asyncio
async def test_once_per_day_false_dedupes_in_process(channels):
    params = NotifyParams(title="只发一次", once_per_day=False)

    await run_notify(params)
    again = await run_notify(params)

    assert again.data.deduped is True
    assert channels.publish.await_count == 1


@pytest.mark.asyncio
async def test_empty_title_fails(channels):
    result = await run_notify(NotifyParams(title="   "))

    assert result.success is False
    assert "标题" in (result.error or "")
