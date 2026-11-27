"""批量 Cookie 编排服务的逐项隔离测试。

不启动浏览器：会话池与 ``BrowserDataService`` 全部用 monkeypatch 替换，
只验证「状态映射 + 单项失败不抛异常」这一层编排逻辑。
"""

from types import SimpleNamespace

import pytest
from playwright._impl._errors import TargetClosedError

from app.models.common.exceptions.base_exception import BrowserNotStartedException
from app.models.runtime.browser_operation import (
    ClearLoginStateResponse,
    ExportCookiesResponse,
)
from app.services.RPA_browser.session.browser_data_service import BrowserDataService
from app.services.RPA_browser.session.cookie_batch_service import CookieBatchService
from app.services.RPA_browser.session.live_service import LiveService, live_service


async def _touch_ok(*args, **kwargs) -> bool:
    return True


class _FakeBrowserContext:
    """存活探测只调 cookies()，这里返回空列表即可。"""

    async def cookies(self) -> list[object]:
        return []


def _running_entry(*, workflow_running: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        browser_running=True,
        is_workflow_running=workflow_running,
        browser_session=SimpleNamespace(browser_context=_FakeBrowserContext()),
    )


# ============ 存活探测 ============


class _ClosedBrowserContext:
    async def cookies(self) -> list[object]:
        raise TargetClosedError("Target page, context or browser has been closed")


class _FlakyBrowserContext:
    async def cookies(self) -> list[object]:
        raise RuntimeError("protocol timeout")


def _entry_with(browser_running: bool, context: object) -> SimpleNamespace:
    return SimpleNamespace(
        browser_running=browser_running,
        browser_session=SimpleNamespace(browser_context=context),
    )


async def test_is_session_alive_without_pages() -> None:
    """浏览器在跑但页面全关（``browser_running=False``）不能被判死。"""
    entry = _entry_with(False, _FakeBrowserContext())
    assert await LiveService.is_session_alive(entry) is True


async def test_is_session_alive_target_closed() -> None:
    entry = _entry_with(True, _ClosedBrowserContext())
    assert await LiveService.is_session_alive(entry) is False


async def test_is_session_alive_tolerates_transient_error() -> None:
    """超时 / 临时协议错误不能误杀会话，交给真实操作去暴露问题。"""
    entry = _entry_with(True, _FlakyBrowserContext())
    assert await LiveService.is_session_alive(entry) is True


# ============ 批量导出 ============


async def test_export_invalid_browser_id() -> None:
    result = await CookieBatchService.export_for_browser(1, "not-a-number")
    assert result.status == "invalid"
    assert result.cookie_count == 0
    assert result.cookies == []


async def test_export_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args, **kwargs):
        raise BrowserNotStartedException()

    monkeypatch.setattr(live_service, "get_browser_session_entry", _raise)

    result = await CookieBatchService.export_for_browser(1, "1001")
    assert result.status == "not_running"


async def test_export_success(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _running_entry()
    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: entry
    )
    monkeypatch.setattr(live_service, "touch", _touch_ok)

    async def _export(context, *, domains=None) -> ExportCookiesResponse:
        assert domains == ["a.com"]
        return ExportCookiesResponse(
            cookie_count=2, matched_domains=["a.com"], message="已导出 2 条"
        )

    monkeypatch.setattr(BrowserDataService, "export_cookies", _export)

    result = await CookieBatchService.export_for_browser(1, "1001", domains=["a.com"])
    assert result.status == "success"
    assert result.cookie_count == 2
    assert result.matched_domains == ["a.com"]


async def test_export_internal_error_maps_to_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _running_entry()
    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: entry
    )
    monkeypatch.setattr(live_service, "touch", _touch_ok)

    async def _export(context, *, domains=None) -> ExportCookiesResponse:
        raise RuntimeError("boom")

    monkeypatch.setattr(BrowserDataService, "export_cookies", _export)

    result = await CookieBatchService.export_for_browser(1, "1001")
    assert result.status == "failed"
    assert "boom" in result.message


async def test_export_launches_when_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _running_entry()

    def _not_started(*args, **kwargs):
        raise BrowserNotStartedException()

    async def _launch(*args, **kwargs) -> SimpleNamespace:
        return entry

    monkeypatch.setattr(live_service, "get_browser_session_entry", _not_started)
    monkeypatch.setattr(
        live_service, "get_or_create_browser_session_entry", _launch
    )
    monkeypatch.setattr(live_service, "touch", _touch_ok)

    async def _export(context, *, domains=None) -> ExportCookiesResponse:
        return ExportCookiesResponse(
            cookie_count=1, matched_domains=["a.com"], message="已导出 1 条 cookie"
        )

    monkeypatch.setattr(BrowserDataService, "export_cookies", _export)

    result = await CookieBatchService.export_for_browser(
        1, "1001", launch_if_not_running=True
    )
    assert result.status == "success"
    assert result.launched is True
    assert "本次启动浏览器" in result.message


async def test_export_launch_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _not_started(*args, **kwargs):
        raise BrowserNotStartedException()

    async def _launch(*args, **kwargs) -> SimpleNamespace:
        raise RuntimeError("launch boom")

    monkeypatch.setattr(live_service, "get_browser_session_entry", _not_started)
    monkeypatch.setattr(
        live_service, "get_or_create_browser_session_entry", _launch
    )

    result = await CookieBatchService.export_for_browser(
        1, "1001", launch_if_not_running=True
    )
    assert result.status == "launch_failed"
    assert "launch boom" in result.message


async def test_export_recovers_dead_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """entry 还在池里但浏览器已关闭：清理僵尸会话后重新启动再导出。"""
    entry = _running_entry()
    released: list[int] = []

    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: entry
    )

    async def _not_alive(target_entry: SimpleNamespace) -> bool:
        return False

    monkeypatch.setattr(live_service, "is_session_alive", _not_alive)

    async def _release(mid: int, browser_id: int) -> bool:
        released.append(browser_id)
        return True

    monkeypatch.setattr(live_service, "release_browser_session", _release)

    async def _launch(*args, **kwargs) -> SimpleNamespace:
        return entry

    monkeypatch.setattr(
        live_service, "get_or_create_browser_session_entry", _launch
    )
    monkeypatch.setattr(live_service, "touch", _touch_ok)

    async def _export(context, *, domains=None) -> ExportCookiesResponse:
        return ExportCookiesResponse(
            cookie_count=1, matched_domains=["a.com"], message="已导出 1 条 cookie"
        )

    monkeypatch.setattr(BrowserDataService, "export_cookies", _export)

    result = await CookieBatchService.export_for_browser(
        1, "1001", launch_if_not_running=True
    )
    assert result.status == "success"
    assert result.launched is True
    assert released == [1001]


async def test_export_recovers_after_target_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读取瞬间被关闭（TargetClosedError）：清理后重建会话并重试一次。"""
    entry = _running_entry()
    calls = {"export": 0}

    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: entry
    )
    monkeypatch.setattr(live_service, "touch", _touch_ok)

    async def _release(mid: int, browser_id: int) -> bool:
        return True

    monkeypatch.setattr(live_service, "release_browser_session", _release)

    async def _launch(*args, **kwargs) -> SimpleNamespace:
        return entry

    monkeypatch.setattr(
        live_service, "get_or_create_browser_session_entry", _launch
    )

    async def _export(context, *, domains=None) -> ExportCookiesResponse:
        calls["export"] += 1
        if calls["export"] == 1:
            raise TargetClosedError(
                "Target page, context or browser has been closed"
            )
        return ExportCookiesResponse(
            cookie_count=1, matched_domains=["a.com"], message="已导出 1 条 cookie"
        )

    monkeypatch.setattr(BrowserDataService, "export_cookies", _export)

    result = await CookieBatchService.export_for_browser(
        1, "1001", launch_if_not_running=True
    )
    assert result.status == "success"
    assert calls["export"] == 2
    assert result.launched is True


# ============ 批量清空 ============


async def test_clear_workflow_running(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _running_entry(workflow_running=True)
    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: entry
    )

    result = await CookieBatchService.clear_for_browser(1, "1001")
    assert result.status == "workflow_running"
    assert result.cleared_pages == 0


async def test_clear_success(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _running_entry()
    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda *a, **k: entry
    )
    monkeypatch.setattr(live_service, "touch", _touch_ok)

    async def _clear(
        browser_session, *, domains=None, reload_pages=True
    ) -> ClearLoginStateResponse:
        assert domains is None
        assert reload_pages is True
        return ClearLoginStateResponse(
            cleared_cookies=True,
            cleared_pages=2,
            reloaded_pages=2,
            affected_domains=3,
            message="已退出登录",
        )

    monkeypatch.setattr(BrowserDataService, "clear_login_state", _clear)

    result = await CookieBatchService.clear_for_browser(1, "1001")
    assert result.status == "success"
    assert result.affected_domains == 3
    assert result.cleared_pages == 2
