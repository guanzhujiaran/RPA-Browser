"""Cookie 导入的会话守卫测试（不启动浏览器）。

回归：手动关闭浏览器后 entry 仍在会话池里，`add_cookies` 抛
``Target page, context or browser has been closed``，导入接口把它当成「写入失败」。
现在先做存活探测、清理僵尸会话，按 ``not_running`` 处理；
允许顺带启动时重建会话并重试一次。
"""

from types import SimpleNamespace

import pytest
from playwright._impl._errors import TargetClosedError

from app.models.runtime.browser_operation import CookieData, CookieWriteData
from app.services.RPA_browser.session.browser_data_service import BrowserDataService
from app.services.RPA_browser.session.cookie_import_service import CookieImportService
from app.services.RPA_browser.session.live_service import live_service


def _entry(*, workflow_running: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        is_workflow_running=workflow_running,
        browser_session=SimpleNamespace(browser_context=object()),
    )


def _cookies() -> list[CookieData]:
    return [CookieData(name="a", value="b", domain=".example.com")]


async def _alive(_entry: SimpleNamespace) -> bool:
    return True


async def _dead(_entry: SimpleNamespace) -> bool:
    return False


async def _release(_mid: int, _browser_id: int) -> bool:
    return True


async def _touch(*_args, **_kwargs) -> bool:
    return True


def _write_result() -> CookieWriteData:
    return CookieWriteData(
        imported_count=1,
        reloaded_pages=0,
        session_cookie_count=0,
    )


async def test_import_dead_session_returns_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """浏览器被手动关闭且未勾选顺带启动：按未启动处理，不是写入失败。"""
    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: _entry()
    )
    monkeypatch.setattr(live_service, "is_session_alive", _dead)
    monkeypatch.setattr(live_service, "release_browser_session", _release)

    result = await CookieImportService.import_for_browser(1, "1001", _cookies())

    assert result.status == "not_running"
    assert "已关闭" in result.message


async def test_import_dead_session_launches_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry()

    async def _launch(*_args, **_kwargs) -> SimpleNamespace:
        return entry

    async def _import(_context, _cookies, *, reload_pages=True) -> CookieWriteData:
        return _write_result()

    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: entry
    )
    monkeypatch.setattr(live_service, "is_session_alive", _dead)
    monkeypatch.setattr(live_service, "release_browser_session", _release)
    monkeypatch.setattr(
        live_service, "get_or_create_browser_session_entry", _launch
    )
    monkeypatch.setattr(live_service, "touch", _touch)
    monkeypatch.setattr(BrowserDataService, "import_cookies", _import)

    result = await CookieImportService.import_for_browser(
        1, "1001", _cookies(), launch_if_not_running=True
    )

    assert result.status == "success"
    assert result.launched is True


async def test_import_retries_after_target_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """写入瞬间被关闭：清理僵尸会话、重建后重试一次。"""
    entry = _entry()
    calls = {"import": 0}

    async def _launch(*_args, **_kwargs) -> SimpleNamespace:
        return entry

    async def _import(_context, _cookies, *, reload_pages=True) -> CookieWriteData:
        calls["import"] += 1
        if calls["import"] == 1:
            raise TargetClosedError(
                "Target page, context or browser has been closed"
            )
        return _write_result()

    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: entry
    )
    monkeypatch.setattr(live_service, "is_session_alive", _alive)
    monkeypatch.setattr(live_service, "release_browser_session", _release)
    monkeypatch.setattr(
        live_service, "get_or_create_browser_session_entry", _launch
    )
    monkeypatch.setattr(live_service, "touch", _touch)
    monkeypatch.setattr(BrowserDataService, "import_cookies", _import)

    result = await CookieImportService.import_for_browser(
        1, "1001", _cookies(), launch_if_not_running=True
    )

    assert result.status == "success"
    assert calls["import"] == 2
    assert result.launched is True


async def test_import_target_closed_without_launch_returns_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry()

    async def _import(_context, _cookies, *, reload_pages=True) -> CookieWriteData:
        raise TargetClosedError(
            "Target page, context or browser has been closed"
        )

    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: entry
    )
    monkeypatch.setattr(live_service, "is_session_alive", _alive)
    monkeypatch.setattr(live_service, "release_browser_session", _release)
    monkeypatch.setattr(live_service, "touch", _touch)
    monkeypatch.setattr(BrowserDataService, "import_cookies", _import)

    result = await CookieImportService.import_for_browser(1, "1001", _cookies())

    assert result.status == "not_running"
    assert "已关闭" in result.message
