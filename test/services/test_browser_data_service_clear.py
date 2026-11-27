"""按站点清空登录态的过滤逻辑测试（不启动浏览器）。

覆盖三条语义：
1. 不传 domains = 清空全部（换号，现行为不变）；
2. 指定站点 = 只清匹配的 cookie 与 origin 匹配页面的 web storage；
3. domains 全部非法 = 拒绝，不退化成「清空全部」。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.models.common.exceptions.base_exception import InvalidParamException
from app.services.RPA_browser.session.browser_data_service import BrowserDataService


class _FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.storage_cleared = False
        self.reloaded = False

    async def evaluate(self, _script: str) -> None:
        self.storage_cleared = True

    async def reload(self, **_kwargs: object) -> None:
        self.reloaded = True


class _FakeContext:
    def __init__(self, cookies: list[dict[str, object]]) -> None:
        self._cookies = cookies
        self.cleared_all = False
        self.cleared_domains: list[str] = []
        self.permissions_cleared = False

    async def cookies(self) -> list[dict[str, object]]:
        return self._cookies

    async def clear_cookies(self, *, domain: str | None = None) -> None:
        if domain is None:
            self.cleared_all = True
        else:
            self.cleared_domains.append(domain)

    async def clear_permissions(self) -> None:
        self.permissions_cleared = True


def _session(context: _FakeContext, pages: list[_FakePage]) -> SimpleNamespace:
    return SimpleNamespace(browser_context=context, all_pages=pages)


async def test_clear_all_by_default() -> None:
    context = _FakeContext(
        [{"domain": ".bilibili.com"}, {"domain": ".other.com"}]
    )
    page = _FakePage("https://www.bilibili.com/")

    data = await BrowserDataService.clear_login_state(_session(context, [page]))

    assert context.cleared_all is True
    assert context.cleared_domains == []
    assert context.permissions_cleared is True
    assert data.cleared_cookie_count == 2
    assert data.matched_domains == [".bilibili.com", ".other.com"]
    assert page.storage_cleared is True


async def test_clear_scoped_to_domains() -> None:
    context = _FakeContext(
        [
            {"domain": ".bilibili.com"},
            {"domain": "api.bilibili.com"},
            {"domain": ".other.com"},
        ]
    )
    bili_page = _FakePage("https://www.bilibili.com/")
    other_page = _FakePage("https://other.com/")

    data = await BrowserDataService.clear_login_state(
        _session(context, [bili_page, other_page]),
        domains=["bilibili.com"],
    )

    assert context.cleared_all is False
    assert set(context.cleared_domains) == {".bilibili.com", "api.bilibili.com"}
    # 权限无法按站点清，仍然整体重置（宁多清，不留越权残留）
    assert context.permissions_cleared is True
    assert data.cleared_cookie_count == 2
    assert data.affected_domains == 2
    assert data.matched_domains == [".bilibili.com", "api.bilibili.com"]

    # 只有 origin 匹配的页面被清 storage
    assert bili_page.storage_cleared is True
    assert other_page.storage_cleared is False
    assert data.reloaded_pages == 1

    # 接口返回时刷新还没执行（证明 reload 没有阻塞响应，不会撞网关超时）
    assert bili_page.reloaded is False

    # 后台任务被调度后才真正刷新
    await asyncio.sleep(0.05)
    assert bili_page.reloaded is True
    assert other_page.reloaded is False


async def test_clear_rejects_invalid_domains() -> None:
    context = _FakeContext([{"domain": ".bilibili.com"}])

    with pytest.raises(InvalidParamException):
        await BrowserDataService.clear_login_state(
            _session(context, []), domains=["", "   "]
        )
