"""CookieData 的过期时间归一化测试。

Playwright 的 ``add_cookies`` 只接受 ``expires = -1`` 或正数；
``0``（Chrome 扩展里常表示会话 cookie）/ 负数 / NaN / Inf 会让**整批**写入失败，
因此模型层必须提前归一化成 ``-1``，并在导入响应里通过 ``session_cookie_count`` 告知。
"""

import pytest

from app.models.runtime.browser_operation import (
    MAX_COOKIE_EXPIRES,
    CookieData,
)
from app.services.RPA_browser.session.browser_data_service import BrowserDataService


def _cookie(**kwargs: object) -> CookieData:
    return CookieData(
        name="probe",
        value="v",
        domain=".bilibili.com",
        **kwargs,
    )


@pytest.mark.parametrize(
    "expires",
    [None, 0, -2, -100, float("nan"), float("inf"), float("-inf")],
)
def test_invalid_expires_normalized_to_session(expires: float | None) -> None:
    assert _cookie(expires=expires).expires == -1.0


def test_null_expires_payload_is_session() -> None:
    """扩展导出的 ``expires: null`` 不能被透传给 add_cookies。"""
    payload = BrowserDataService._to_playwright_cookie(_cookie(expires=None))
    assert payload["expires"] == -1.0


def test_positive_expires_kept() -> None:
    assert _cookie(expires=1893456000).expires == 1893456000.0


def test_expiration_date_alias_is_respected() -> None:
    assert _cookie(expirationDate=1893456000).expires == 1893456000.0


def test_missing_expires_is_session_cookie() -> None:
    assert _cookie().expires == -1.0


def test_playwright_payload_uses_normalized_expires() -> None:
    """归一化必须落到真正传给 add_cookies 的 payload 上。"""
    payload = BrowserDataService._to_playwright_cookie(_cookie(expires=0))
    assert payload["expires"] == -1.0


def test_millisecond_timestamp_converted_to_seconds() -> None:
    """13 位毫秒时间戳（Date.now / 部分扩展）会被换算成秒。"""
    assert _cookie(expires=1893456000000).expires == 1893456000.0
    payload = BrowserDataService._to_playwright_cookie(
        _cookie(expires=1893456000000)
    )
    assert payload["expires"] == 1893456000.0


def test_expires_beyond_playwright_limit_falls_back_to_session() -> None:
    """超过 Playwright 上限（253402300799）的值不能透传，否则整批写入失败。"""
    assert _cookie(expires=MAX_COOKIE_EXPIRES * 1000 + 1).expires == -1.0
