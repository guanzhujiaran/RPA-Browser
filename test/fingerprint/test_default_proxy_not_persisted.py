"""代理落库口径的回归测试。

项目默认代理（环境变量 ``default_proxy_server``，dev 里是 ``127.0.0.1:3128``）只允许在
启动浏览器时做运行时兜底，不能进入服务端默认设置/浏览器指纹，否则会被固化到数据库。
"""

from __future__ import annotations

from app.models.database.browser.info import (
    UserBrowserDefaultSetting,
    UserBrowserDefaultSettingResponse,
    UserBrowserServerSideDefaultSetting,
)


def test_server_side_default_setting_has_no_proxy() -> None:
    """服务端默认设置不应携带环境变量里的默认代理。"""
    server_defaults = UserBrowserServerSideDefaultSetting(mid=-1)

    assert server_defaults.default_proxy_server is None


def test_server_side_default_setting_response_has_no_proxy() -> None:
    """/get_server_default_settings 返回给前端的默认代理必须为空。"""
    response = UserBrowserDefaultSettingResponse(
        **UserBrowserServerSideDefaultSetting(mid=-1).model_dump()
    )

    assert response.default_proxy_server is None


def test_table_field_default_is_none() -> None:
    """数据库模型的默认值不能绑定环境变量。"""
    assert (
        UserBrowserDefaultSetting.model_fields["default_proxy_server"].default is None
    )
