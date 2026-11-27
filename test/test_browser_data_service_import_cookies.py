"""Cookie 写入（``BrowserDataService.import_cookies``）与导入编排（``CookieImportService``）单测。

覆盖两类关注点：

1. 写入层的归一化与容错：sameSite 大小写、``sameSite=None`` 必须配 secure、
   domain 保留前导点、reload 失败不影响「已写入」这个事实；
2. 编排层的运行态语义：未启动默认不启动、显式要求才启动、工作流执行期拒绝、
   browser_id 非法不触达浏览器。
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.common.exceptions.base_exception import (
    BrowserNotStartedException,
    InvalidParamException,
)
from app.controller.v1.browser_control.operation.router import (
    _pair_cookie_groups,
)
from app.models.runtime.browser_operation import (
    CookieData,
    ImportCookiesBatchRequest,
    ImportCookiesRequest,
)
from app.services.RPA_browser.session.browser_data_service import BrowserDataService
from app.services.RPA_browser.session.cookie_import_service import CookieImportService
from app.services.RPA_browser.session.live_service import live_service


class _FakePage:
    def __init__(self, *, fail_reload: bool = False) -> None:
        self.reload_count = 0
        self.fail_reload = fail_reload

    async def reload(self) -> None:
        if self.fail_reload:
            raise RuntimeError("reload failed")
        self.reload_count += 1


class _FakeBrowserContext:
    def __init__(self, pages: list[_FakePage] | None = None) -> None:
        self.pages = pages or []
        self.added: list = []

    async def add_cookies(self, cookies) -> None:  # noqa: ANN001
        self.added.extend(cookies)


def _sessdata() -> CookieData:
    return CookieData(
        name="SESSDATA",
        value="v",
        domain=".bilibili.com",
        httpOnly=True,
        secure=True,
        sameSite="None",
    )


# ============ 写入层 ============


async def test_import_writes_cookies_and_reloads_pages() -> None:
    page = _FakePage()
    context = _FakeBrowserContext([page])

    data = await BrowserDataService.import_cookies(context, [_sessdata()])  # type: ignore[arg-type]

    assert data.imported_count == 1
    assert data.reloaded_pages == 1
    assert page.reload_count == 1
    assert context.added[0]["name"] == "SESSDATA"


async def test_import_skips_reload_when_disabled() -> None:
    page = _FakePage()
    context = _FakeBrowserContext([page])

    data = await BrowserDataService.import_cookies(
        context,  # type: ignore[arg-type]
        [_sessdata()],
        reload_pages=False,
    )

    assert data.reloaded_pages == 0
    assert page.reload_count == 0


async def test_import_reload_failure_does_not_undo_write() -> None:
    """刷新只是让页面看到新登录态，失败不能把「已写入」变成失败。"""
    page = _FakePage(fail_reload=True)
    context = _FakeBrowserContext([page])

    data = await BrowserDataService.import_cookies(context, [_sessdata()])  # type: ignore[arg-type]

    assert data.imported_count == 1
    assert data.reloaded_pages == 0


async def test_import_normalizes_same_site_case() -> None:
    context = _FakeBrowserContext()

    await BrowserDataService.import_cookies(
        context,  # type: ignore[arg-type]
        [CookieData(name="a", value="b", domain="example.com", sameSite="lax")],
        reload_pages=False,
    )

    assert context.added[0]["sameSite"] == "Lax"


async def test_import_forces_secure_when_same_site_none() -> None:
    """sameSite=None 不配 secure 会被 Chromium 直接拒收，这里自动补齐。"""
    context = _FakeBrowserContext()

    await BrowserDataService.import_cookies(
        context,  # type: ignore[arg-type]
        [
            CookieData(
                name="a",
                value="b",
                domain="example.com",
                sameSite="none",
                secure=False,
            )
        ],
        reload_pages=False,
    )

    assert context.added[0]["sameSite"] == "None"
    assert context.added[0]["secure"] is True


async def test_import_counts_session_cookies() -> None:
    """会话 cookie（expires < 0）会被 Chromium 重启时丢弃，必须如实计数。"""
    context = _FakeBrowserContext()

    data = await BrowserDataService.import_cookies(
        context,  # type: ignore[arg-type]
        [
            _sessdata(),  # expires 默认 -1
            CookieData(
                name="persist",
                value="v",
                domain="example.com",
                expires=1900000000,
            ),
        ],
        reload_pages=False,
    )

    assert data.imported_count == 2
    assert data.session_cookie_count == 1


async def test_import_keeps_leading_dot_in_domain() -> None:
    """前导点表示「对子域同样生效」，写入时不能像过滤时那样 strip 掉。"""
    context = _FakeBrowserContext()

    await BrowserDataService.import_cookies(
        context, [_sessdata()], reload_pages=False  # type: ignore[arg-type]
    )

    assert context.added[0]["domain"] == ".bilibili.com"


async def test_import_strips_protocol_from_domain() -> None:
    context = _FakeBrowserContext()

    await BrowserDataService.import_cookies(
        context,  # type: ignore[arg-type]
        [CookieData(name="a", value="b", domain="https://www.bilibili.com/x")],
        reload_pages=False,
    )

    assert context.added[0]["domain"] == "www.bilibili.com"


async def test_import_rejects_cookie_without_domain() -> None:
    """缺 domain 的 cookie 会让整批写入失败，必须提前明确报错。"""
    context = _FakeBrowserContext()

    with pytest.raises(InvalidParamException):
        await BrowserDataService.import_cookies(
            context,  # type: ignore[arg-type]
            [CookieData(name="a", value="b", domain="   ")],
            reload_pages=False,
        )

    assert context.added == []


def test_import_request_requires_at_least_one_cookie() -> None:
    with pytest.raises(ValidationError):
        ImportCookiesRequest(cookies=[])


def test_batch_request_limits_browsers() -> None:
    """单次批量上限 50 个浏览器：避免一次请求长时间占用启动队列。"""
    group = [_sessdata()]

    with pytest.raises(ValidationError):
        ImportCookiesBatchRequest(browser_ids=[], cookie_groups=[group])

    with pytest.raises(ValidationError):
        ImportCookiesBatchRequest(
            browser_ids=[str(index) for index in range(51)],
            cookie_groups=[group] * 51,
        )


def test_batch_request_rejects_empty_group() -> None:
    """空组会静默占位，导致后面的组整体错位，必须提前拒绝。"""
    with pytest.raises(ValidationError):
        ImportCookiesBatchRequest(browser_ids=["1"], cookie_groups=[[]])


def test_batch_request_allows_count_mismatch() -> None:
    """数量不匹配是允许的（尽力导入），由接口统计未使用 / 未分配。"""
    request = ImportCookiesBatchRequest(
        browser_ids=["1", "2"],
        cookie_groups=[[_sessdata()]],
    )

    assert len(request.browser_ids) == 2
    assert len(request.cookie_groups) == 1


# ============ 配对与不匹配统计 ============


def test_pair_cookie_groups_exact_match() -> None:
    pairs, unassigned, unused_groups, unused_count = _pair_cookie_groups(
        ["1", "2"], [[_sessdata()], [_sessdata(), _sessdata()]]
    )

    assert len(pairs) == 2
    assert unassigned == []
    assert unused_groups == []
    assert unused_count == 0


def test_pair_cookie_groups_more_groups_than_browsers() -> None:
    """多出的整组整体忽略，并统计其中共多少条 cookie 没用上。"""
    pairs, unassigned, unused_groups, unused_count = _pair_cookie_groups(
        ["1"], [[_sessdata()], [_sessdata(), _sessdata()]]
    )

    assert len(pairs) == 1
    assert unassigned == []
    assert len(unused_groups) == 1
    assert unused_count == 2


def test_pair_cookie_groups_more_browsers_than_groups() -> None:
    """多出的浏览器标记未分配，不写入任何 cookie。"""
    pairs, unassigned, unused_groups, unused_count = _pair_cookie_groups(
        ["1", "2", "3"], [[_sessdata()]]
    )

    assert len(pairs) == 1
    assert unassigned == ["2", "3"]
    assert unused_groups == []
    assert unused_count == 0


# ============ 编排层 ============


def _entry(context: _FakeBrowserContext, *, workflow_running: bool = False):
    return SimpleNamespace(
        is_workflow_running=workflow_running,
        browser_session=SimpleNamespace(browser_context=context),
    )


async def test_import_does_not_launch_by_default(monkeypatch) -> None:  # noqa: ANN001
    def _not_started(_mid, _browser_id):  # noqa: ANN001
        raise BrowserNotStartedException()

    monkeypatch.setattr(live_service, "get_browser_session_entry", _not_started)

    result = await CookieImportService.import_for_browser(1, "123", [_sessdata()])

    assert result.status == "not_running"
    assert result.launched is False
    assert result.imported_count == 0


async def test_import_launches_when_explicitly_requested(monkeypatch) -> None:  # noqa: ANN001
    context = _FakeBrowserContext()
    entry = _entry(context)

    def _not_started(_mid, _browser_id):  # noqa: ANN001
        raise BrowserNotStartedException()

    async def _launch(*args, **kwargs):  # noqa: ANN002, ANN003
        return entry

    monkeypatch.setattr(live_service, "get_browser_session_entry", _not_started)
    monkeypatch.setattr(
        live_service, "get_or_create_browser_session_entry", _launch
    )

    result = await CookieImportService.import_for_browser(
        1, "123", [_sessdata()], launch_if_not_running=True
    )

    assert result.status == "success"
    assert result.launched is True
    assert result.imported_count == 1


async def test_import_reports_launch_failure(monkeypatch) -> None:  # noqa: ANN001
    def _not_started(_mid, _browser_id):  # noqa: ANN001
        raise BrowserNotStartedException()

    async def _launch(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("内存不足")

    monkeypatch.setattr(live_service, "get_browser_session_entry", _not_started)
    monkeypatch.setattr(
        live_service, "get_or_create_browser_session_entry", _launch
    )

    result = await CookieImportService.import_for_browser(
        1, "123", [_sessdata()], launch_if_not_running=True
    )

    assert result.status == "launch_failed"
    assert "内存不足" in result.message


async def test_import_rejected_while_workflow_running(monkeypatch) -> None:  # noqa: ANN001
    entry = _entry(_FakeBrowserContext(), workflow_running=True)
    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda _mid, _bid: entry
    )

    result = await CookieImportService.import_for_browser(1, "123", [_sessdata()])

    assert result.status == "workflow_running"
    assert result.imported_count == 0


async def test_import_invalid_browser_id_never_touches_browser() -> None:
    result = await CookieImportService.import_for_browser(1, "not-a-number", [_sessdata()])

    assert result.status == "invalid"
    assert result.imported_count == 0


async def test_import_success_status(monkeypatch) -> None:  # noqa: ANN001
    context = _FakeBrowserContext()
    entry = _entry(context)
    monkeypatch.setattr(
        live_service, "get_browser_session_entry", lambda _mid, _bid: entry
    )

    result = await CookieImportService.import_for_browser(1, "123", [_sessdata()])

    assert result.status == "success"
    assert result.launched is False
    assert result.imported_count == 1
    assert result.session_cookie_count == 1
    assert "重启后会失效" in result.message
    assert len(context.added) == 1
