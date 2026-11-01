"""代理绕过列表（per-browser proxy bypass）的回归测试。

覆盖三件事：

1. 归一化口径：分号/空格/换行 → 逗号、去重、带协议前缀直接报错；
2. 解析优先级：浏览器单独配置 → 服务端环境变量兜底；
3. 下发口径：绕过列表只能走 playwright 的 ``proxy.bypass``（driver 拼
   ``--proxy-bypass-list``），绝不能出现在 chromium 启动 ``args`` 里。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.models.core.browser.fingerprint import BaseFingerprintBrowserInitParams
from app.utils.http.proxy_utils import (
    normalize_proxy_bypass,
    resolve_browser_proxy_bypass,
)
from botright.playwright_mock.browser import _build_proxy_option


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("*.bilibili.com", "*.bilibili.com"),
        ("*.bilibili.com;127.0.0.1", "*.bilibili.com,127.0.0.1"),
        (" a.com , b.com ", "a.com,b.com"),
        ("a.com\na.com", "a.com"),
        ("a.com;;b.com", "a.com,b.com"),
    ],
)
def test_normalize_proxy_bypass(raw: str | None, expected: str | None) -> None:
    assert normalize_proxy_bypass(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["https://bilibili.com", "*.a.com, http://b.com", "socks5://127.0.0.1:1080"],
)
def test_normalize_proxy_bypass_rejects_scheme(raw: str) -> None:
    """绕过规则只按 host 匹配，用户误写完整 URL 时必须报错而不是静默失效。"""
    with pytest.raises(ValueError):
        normalize_proxy_bypass(raw)


def test_resolve_prefers_browser_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "browser_proxy_bypass", "env.com")
    assert resolve_browser_proxy_bypass("*.bili.com") == "*.bili.com"


def test_resolve_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "browser_proxy_bypass", "*.bili.com;127.0.0.1")
    assert resolve_browser_proxy_bypass(None) == "*.bili.com,127.0.0.1"


def test_resolve_returns_none_when_nothing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "browser_proxy_bypass", "")
    assert resolve_browser_proxy_bypass(None) is None


def test_bypass_is_not_rendered_into_cli_args() -> None:
    """proxy_bypass 是 launch kwarg（proxy.bypass），不能变成 --proxy-bypass=... 参数。"""
    args = BaseFingerprintBrowserInitParams(
        proxy_server="127.0.0.1:3128", proxy_bypass="*.bili.com"
    ).fp_2_args_list()

    assert not [arg for arg in args if "proxy" in arg]


def test_proxy_option_without_proxy_ignores_bypass() -> None:
    """直连（没有 proxy_server）时绕过列表无意义，不能传出只含 bypass 的 proxy 选项。"""
    proxy = SimpleNamespace(browser_proxy=None)

    assert _build_proxy_option(proxy, "*.bili.com") is None


def test_proxy_option_adds_bypass() -> None:
    proxy = SimpleNamespace(browser_proxy={"server": "127.0.0.1:3128"})

    assert _build_proxy_option(proxy, "*.bili.com,127.0.0.1") == {
        "server": "127.0.0.1:3128",
        "bypass": "*.bili.com,127.0.0.1",
    }


def test_proxy_option_keeps_proxy_untouched_without_bypass() -> None:
    proxy = SimpleNamespace(browser_proxy={"server": "127.0.0.1:3128"})

    assert _build_proxy_option(proxy, None) == {"server": "127.0.0.1:3128"}
