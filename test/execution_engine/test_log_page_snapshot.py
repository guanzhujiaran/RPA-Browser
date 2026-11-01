"""页面关键元素快照采集单测（离线）

对应报错时的页面快照能力：``ActionLogOption.record_page_snapshot``（默认关、
仅失败时采集）+ ``action_logger._capture_page_snapshot``（站点无关，失败不影响主流程）。

覆盖：
- ``_capture_page_snapshot``：正常返回 dict、page 为 None / 无 evaluate_handle /
  JS 返回非 dict 时安全返回 None；
- 采集失败（抛异常）时返回 None，不向外抛；
- debug 归一化强制开启 ``record_page_snapshot``。
"""

from types import SimpleNamespace

import pytest

from app.services.execution import action_logger
from app.services.execution.action_logger import (
    ActionLogContext,
    _capture_page_snapshot,
    _do_save,
    _normalize_debug,
)
from app.models.execution.action_params import ActionLogOption


class _FakeHandle:
    def __init__(self, value):
        self._value = value

    async def json_value(self):
        return self._value


class _FakePage:
    def __init__(self, value=None, raise_on_eval=False):
        self._value = value
        self._raise = raise_on_eval

    async def evaluate_handle(self, expression):
        if self._raise:
            raise RuntimeError("page closed")
        return _FakeHandle(self._value)


@pytest.mark.asyncio
async def test_capture_returns_dict():
    page = _FakePage({"url": "https://x", "title": "t", "elements": []})
    snap = await _capture_page_snapshot(page, 4000)
    assert snap == {"url": "https://x", "title": "t", "elements": []}


@pytest.mark.asyncio
async def test_capture_none_when_page_none():
    assert await _capture_page_snapshot(None, 4000) is None


@pytest.mark.asyncio
async def test_capture_none_when_no_evaluate_handle():
    page = SimpleNamespace()  # 无 evaluate_handle
    assert await _capture_page_snapshot(page, 4000) is None


@pytest.mark.asyncio
async def test_capture_none_when_not_dict():
    page = _FakePage("not-a-dict")
    assert await _capture_page_snapshot(page, 4000) is None


@pytest.mark.asyncio
async def test_capture_none_on_exception():
    page = _FakePage(raise_on_eval=True)
    assert await _capture_page_snapshot(page, 4000) is None


def test_debug_normalize_enables_page_snapshot():
    option = ActionLogOption(
        enabled=False,
        debug=True,
        record_page_snapshot=False,
    )
    normalized = _normalize_debug(option)
    assert normalized is not None
    assert normalized.record_page_snapshot is True


# ── 落库路径：只有「失败 + 开关开」才写 page_snapshot ──────────────

_SNAPSHOT = {
    "url": "https://x/dynamic/1",
    "title": "动态详情",
    "elements": [{"tag": "button", "selector": "button.pub", "text": "发布"}],
}


def _ctx(page, record_page_snapshot: bool) -> ActionLogContext:
    return ActionLogContext(
        mid="1",
        action_id="click",  # 非复合动作 id，避免解析 DB 配置
        action_name="点击",
        action_type="click",
        page=page,
        log_config=ActionLogOption(
            enabled=True,
            record_params=False,
            record_result=False,
            record_variables=False,
            record_page_snapshot=record_page_snapshot,
        ),
    )


@pytest.mark.asyncio
async def test_do_save_writes_snapshot_only_on_failure(monkeypatch):
    captured: list = []

    async def _fake_persist(record):
        captured.append(record)

    monkeypatch.setattr(action_logger, "_persist", _fake_persist)

    failed = SimpleNamespace(success=False, data=None, logs=[], error="元素未找到", execution_time=0.1)
    await _do_save(_ctx(_FakePage(_SNAPSHOT), True), failed)
    assert captured[-1].page_snapshot == _SNAPSHOT

    ok = SimpleNamespace(success=True, data=None, logs=[], error=None, execution_time=0.1)
    await _do_save(_ctx(_FakePage(_SNAPSHOT), True), ok)
    # 成功不采集：快照只用于排查失败现场
    assert captured[-1].page_snapshot is None


@pytest.mark.asyncio
async def test_do_save_skips_snapshot_when_option_off(monkeypatch):
    captured: list = []

    async def _fake_persist(record):
        captured.append(record)

    monkeypatch.setattr(action_logger, "_persist", _fake_persist)

    failed = SimpleNamespace(success=False, data=None, logs=[], error="元素未找到", execution_time=0.1)
    await _do_save(_ctx(_FakePage(_SNAPSHOT), False), failed)
    assert captured[-1].page_snapshot is None
