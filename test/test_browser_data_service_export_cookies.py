"""浏览器 Cookie 导出（``BrowserDataService.export_cookies``）单元测试。

只验证「读取 + 站点过滤 + 字段映射」，不启动真实浏览器：

- 数据源只能是 profile（``browser_context.cookies()``），服务本身不持久化任何 cookie；
- 站点过滤语义是「访问该站点时实际会携带的 cookie」，包含子域与父域，
  避免导出登录态时漏掉 ``.bilibili.com`` 这类父域 cookie。
"""

import pytest

from app.models.common.exceptions.base_exception import InvalidParamException
from app.services.RPA_browser.session.browser_data_service import BrowserDataService


class _FakeBrowserContext:
    """只实现 ``cookies()`` 的最小替身。"""

    def __init__(self, cookies: list[dict]) -> None:
        self._cookies = cookies

    async def cookies(self) -> list[dict]:
        return self._cookies


_COOKIES: list[dict] = [
    {
        "name": "SESSDATA",
        "value": "sess",
        "domain": ".bilibili.com",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
    },
    {
        "name": "buvid3",
        "value": "buvid",
        "domain": "www.bilibili.com",
        "path": "/",
        "expires": 1800000000.0,
        "httpOnly": False,
        "secure": False,
        "sameSite": "Lax",
    },
    {
        "name": "sid",
        "value": "other",
        "domain": "example.com",
        "path": "/",
        "expires": -1,
        "httpOnly": False,
        "secure": False,
        "sameSite": "Lax",
    },
]


async def test_export_all_cookies() -> None:
    """不传 domains 时导出全部，并按域名去重排序。"""
    result = await BrowserDataService.export_cookies(
        _FakeBrowserContext(_COOKIES)  # type: ignore[arg-type]
    )

    assert result.cookie_count == 3
    assert result.matched_domains == [
        ".bilibili.com",
        "example.com",
        "www.bilibili.com",
    ]


async def test_export_filtered_by_site_includes_sub_domains() -> None:
    """传主域时带出其下所有子域的 cookie。"""
    result = await BrowserDataService.export_cookies(
        _FakeBrowserContext(_COOKIES),  # type: ignore[arg-type]
        domains=["bilibili.com"],
    )

    assert {item.name for item in result.cookies} == {"SESSDATA", "buvid3"}


async def test_export_filtered_by_subdomain_keeps_parent_cookie() -> None:
    """传子域（或整条 URL）时，父域 cookie 同样对它有作用，必须保留。"""
    result = await BrowserDataService.export_cookies(
        _FakeBrowserContext(_COOKIES),  # type: ignore[arg-type]
        domains=["https://www.bilibili.com/video/BV1xx"],
    )

    assert {item.name for item in result.cookies} == {"SESSDATA", "buvid3"}


async def test_export_filtered_excludes_unrelated_domain() -> None:
    """后缀相似但不是同一站点的域名不能误匹配。"""
    result = await BrowserDataService.export_cookies(
        _FakeBrowserContext(_COOKIES),  # type: ignore[arg-type]
        domains=["example.com"],
    )

    assert {item.name for item in result.cookies} == {"sid"}


async def test_export_maps_cookie_fields() -> None:
    """HttpOnly / secure / sameSite / expires 原样带出，不做加工。"""
    result = await BrowserDataService.export_cookies(
        _FakeBrowserContext(_COOKIES),  # type: ignore[arg-type]
        domains=["bilibili.com"],
    )
    sessdata = next(item for item in result.cookies if item.name == "SESSDATA")

    assert sessdata.domain == ".bilibili.com"
    assert sessdata.httpOnly is True
    assert sessdata.secure is True
    assert sessdata.sameSite == "None"
    assert sessdata.expires == -1


async def test_export_rejects_unparsable_domains() -> None:
    """传了 domains 但全部解析失败时必须报参数错误，不能退化成「导出全部」。"""
    with pytest.raises(InvalidParamException):
        await BrowserDataService.export_cookies(
            _FakeBrowserContext(_COOKIES),  # type: ignore[arg-type]
            domains=["", "   "],
        )


async def test_export_returns_empty_when_nothing_matches() -> None:
    """没有匹配域名时返回空结果而不是报错。"""
    result = await BrowserDataService.export_cookies(
        _FakeBrowserContext(_COOKIES),  # type: ignore[arg-type]
        domains=["zhihu.com"],
    )

    assert result.cookie_count == 0
    assert result.cookies == []
    assert result.matched_domains == []


async def test_export_output_is_sorted() -> None:
    """输出顺序稳定，便于前端展示与比对。"""
    result = await BrowserDataService.export_cookies(
        _FakeBrowserContext(_COOKIES)  # type: ignore[arg-type]
    )
    keys = [(item.domain, item.path, item.name) for item in result.cookies]

    assert keys == sorted(keys)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bilibili.com", "bilibili.com"),
        (".bilibili.com", "bilibili.com"),
        ("HTTPS://WWW.Bilibili.com/video/BV1", "www.bilibili.com"),
        ("bilibili.com:443", "bilibili.com"),
        ("127.0.0.1:8080", "127.0.0.1"),
        ("[::1]:8080", "::1"),
        ("bilibili.com/path?x=1#h", "bilibili.com"),
        ("   ", ""),
    ],
)
def test_normalize_site(raw: str, expected: str) -> None:
    """域名 / URL / 端口 / 路径 / 前导点统一归一成小写主机名。"""
    assert BrowserDataService._normalize_site(raw) == expected
