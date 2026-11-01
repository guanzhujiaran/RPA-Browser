"""前端空字符串归一为 None 的回归测试。

前端表单「没填」会传 `""` 而不是 null，必须在入参阶段归一成 None，
不能以空字符串形态落库（ENUM 列会 1265，代理/指纹字段会被空串覆盖）。
"""

from __future__ import annotations

from app.models.database.browser.info import UserBrowserDefaultSettingRequest
from app.models.runtime.api import (
    BrowserFingerprintRenameParams,
    BrowserFingerprintUpsertParams,
)


def test_default_setting_request_blank_proxy_to_none() -> None:
    """默认设置请求里的空代理串按「未提供」处理。"""
    request = UserBrowserDefaultSettingRequest(default_proxy_server="")

    assert request.default_proxy_server is None


def test_default_setting_request_whitespace_to_none() -> None:
    """全空格与空串等价。"""
    request = UserBrowserDefaultSettingRequest(
        default_proxy_server="   ", default_lang="\t"
    )

    assert request.default_proxy_server is None
    assert request.default_lang is None


def test_default_setting_request_keeps_unset_fields() -> None:
    """只有显式传空串的字段参与更新，未传的字段不进入 exclude_unset 结果。"""
    request = UserBrowserDefaultSettingRequest(default_proxy_server="")

    assert request.model_dump(exclude_unset=True) == {"default_proxy_server": None}


def test_fingerprint_upsert_blank_fields_to_none() -> None:
    """指纹 upsert 的空白字段全部归一成 None。"""
    params = BrowserFingerprintUpsertParams(
        browser_id="1",
        proxy_server="",
        lang="  ",
        custom_name="",
        fingerprint_platform="",
        fingerprint_browser="",
    )

    assert params.proxy_server is None
    assert params.lang is None
    assert params.custom_name is None
    assert params.fingerprint_platform is None
    assert params.fingerprint_browser is None


def test_fingerprint_upsert_keeps_enum_normalization() -> None:
    """空白归一不影响枚举大小写归一。"""
    params = BrowserFingerprintUpsertParams(browser_id="1", fingerprint_browser="edge")

    assert params.fingerprint_browser == "Edge"


def test_rename_params_blank_name_to_none() -> None:
    """重命名参数的空名称归一成 None（由服务层判定为「未提供」）。"""
    assert BrowserFingerprintRenameParams(custom_name="   ").custom_name is None
