"""运行通知「通道 × 时机」2×2 矩阵单测（离线）

对应 ``docs/通知动作与运行告警计划书.md`` §E：

- ``NotifyChannelFlags`` 按 4 个开关解析出应投递的通道集合；
- 旧字段（通道混合）在新字段未显式传入时按「双通道」展开，显式传入时以新字段为准；
- ``_notify_run`` 只走开关打开的通道（不再由调用方 getattr 判断），
  全关时不发任何通道且返回 ``False``（不写 ``notified`` 标记）。
"""

import asyncio
from typing import Any

from app.models.database.workflow.models import UserWorkflow
from app.models.workflow.notify_flags import NotifyChannel, NotifyChannelFlags
from app.models.workflow.workflow_requests import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
)
from app.services.execution.workflow import notify as notify_mod


class _NullSession:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeSessionManager:
    """避免测试连库：外部推送分支只用到 async_session 上下文"""

    @staticmethod
    def async_session() -> _NullSession:
        return _NullSession()


class _FakeBrowserService:
    def __init__(self, mid: int):
        self.mid = mid

    async def get_notification_config(self, session: Any) -> object:
        return object()  # 视为「已配置站外渠道」


def _make_workflow(**flags: bool) -> UserWorkflow:
    return UserWorkflow(
        workflow_id=1,
        name="矩阵单测",
        mid=1,
        original_mid=1,
        trigger_type="manual",
        **flags,
    )


class TestChannelFlags:
    def test_channels_by_phase(self):
        flags = NotifyChannelFlags(
            inbox_on_start=False,
            inbox_on_end=True,
            push_on_start=True,
            push_on_end=False,
        )
        assert flags.channels(is_start=True) == frozenset({NotifyChannel.PUSH})
        assert flags.channels(is_start=False) == frozenset({NotifyChannel.INBOX})

    def test_all_off_returns_empty(self):
        flags = NotifyChannelFlags(False, False, False, False)
        assert flags.channels(is_start=True) == frozenset()
        assert flags.channels(is_start=False) == frozenset()

    def test_legacy_derived_value(self):
        flags = NotifyChannelFlags(
            inbox_on_start=False,
            inbox_on_end=True,
            push_on_start=True,
            push_on_end=False,
        )
        assert flags.legacy_on_start is True
        assert flags.legacy_on_end is True


class TestRequestLegacyExpansion:
    def test_create_defaults(self):
        request = WorkflowCreateRequest(name="t")
        assert request.notify_inbox_on_start is False
        assert request.notify_inbox_on_end is True
        assert request.notify_push_on_start is False
        assert request.notify_push_on_end is True

    def test_create_legacy_expands_to_both_channels(self):
        request = WorkflowCreateRequest(
            name="t", notify_on_start=True, notify_on_end=False
        )
        assert request.notify_inbox_on_start is True
        assert request.notify_push_on_start is True
        assert request.notify_inbox_on_end is False
        assert request.notify_push_on_end is False

    def test_update_keeps_none_when_not_provided(self):
        request = WorkflowUpdateRequest(id=1)
        assert request.notify_inbox_on_start is None
        assert request.notify_push_on_end is None
        assert "notify_inbox_on_end" not in request.model_fields_set

    def test_new_field_wins_over_legacy(self):
        request = WorkflowUpdateRequest(
            id=1, notify_on_end=True, notify_push_on_end=False
        )
        # 未显式给出的通道从旧字段展开
        assert request.notify_inbox_on_end is True
        # 显式给出的新字段不被旧字段覆盖
        assert request.notify_push_on_end is False


class TestNotifyRunChannels:
    def _run(
        self,
        monkeypatch,
        *,
        phase: str,
        counter: dict[str, int],
        **flags: bool,
    ) -> bool:
        async def fake_publish_notify(mid, title, content, jump_url=None) -> bool:
            counter["inbox"] += 1
            return True

        async def fake_push_send(**kwargs) -> None:
            counter["push"] += 1

        monkeypatch.setattr(notify_mod, "publish_notify", fake_publish_notify)
        monkeypatch.setattr(notify_mod.push_msg, "send", fake_push_send)
        monkeypatch.setattr(notify_mod, "BrowserService", _FakeBrowserService)
        monkeypatch.setattr(notify_mod, "DatabaseSessionManager", _FakeSessionManager)

        return asyncio.run(
            notify_mod._notify_run(
                workflow=_make_workflow(**flags),
                mid=1,
                browser_id=1,
                run_id="run-matrix-test",
                phase=phase,
            )
        )

    def test_inbox_only_on_end(self, monkeypatch):
        counter = {"inbox": 0, "push": 0}
        sent = self._run(
            monkeypatch,
            phase="success",
            counter=counter,
            notify_inbox_on_end=True,
            notify_push_on_end=False,
        )
        assert counter == {"inbox": 1, "push": 0}
        assert sent is False, "只有站内通道时不标记 notified（该标记只表示站外已发）"

    def test_push_only_on_end(self, monkeypatch):
        counter = {"inbox": 0, "push": 0}
        sent = self._run(
            monkeypatch,
            phase="failure",
            counter=counter,
            notify_inbox_on_end=False,
            notify_push_on_end=True,
        )
        assert counter == {"inbox": 0, "push": 1}
        assert sent is True

    def test_start_phase_ignores_end_switches(self, monkeypatch):
        counter = {"inbox": 0, "push": 0}
        sent = self._run(
            monkeypatch,
            phase="start",
            counter=counter,
            notify_inbox_on_start=False,
            notify_push_on_start=False,
            notify_inbox_on_end=True,
            notify_push_on_end=True,
        )
        assert counter == {"inbox": 0, "push": 0}
        assert sent is False

    def test_all_off_sends_nothing(self, monkeypatch):
        counter = {"inbox": 0, "push": 0}
        sent = self._run(
            monkeypatch,
            phase="cancelled",
            counter=counter,
            notify_inbox_on_end=False,
            notify_push_on_end=False,
        )
        assert counter == {"inbox": 0, "push": 0}
        assert sent is False
