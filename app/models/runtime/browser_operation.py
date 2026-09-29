"""
Runtime 模块 - 浏览器控制「操作」接口模型

`/api/v1/rpa/browser/control/operation/*` 这一组**实时操作接口**的请求 / 响应模型。
（RPA action 的参数模型在 `app/models/execution/action_params.py`，与本模块无关。）
"""

from sqlmodel import SQLModel, Field


# ============ 页面操作请求 ============


class OpenPageRequest(SQLModel):
    """打开页面请求"""

    url: str = Field(..., description="要打开的URL")
    page_index: int = Field(0, description="页面索引，-1表示新建页面")


class ClosePageRequest(SQLModel):
    """关闭页面请求"""

    page_index: int = Field(0, description="要关闭的页面索引")


class SwitchPageRequest(SQLModel):
    """切换页面请求"""

    page_index: int = Field(0, description="目标页面索引")


class GetPageInfoRequest(SQLModel):
    """获取页面信息请求"""

    page_index: int = Field(0, description="页面索引")


# ============ 清空登录态（换号） ============


class ClearLoginStateRequest(SQLModel):
    """清空登录态（换号）请求"""

    reload_pages: bool = Field(
        True,
        description="清空后是否刷新已打开的页面：不刷新的话页面上仍是旧账号的 DOM，用户会以为没生效",
    )


class ClearLoginStateResponse(SQLModel):
    """清空登录态（换号）响应"""

    cleared_cookies: bool = Field(..., description="是否已清空全部站点的 cookie（含 HttpOnly）")
    # 用「页面数」而不是「是否成功」：多页面时部分失败无法用布尔值表达真实情况
    cleared_pages: int = Field(0, description="已清空 web storage 的页面数")
    reloaded_pages: int = Field(0, description="已刷新的页面数")
    affected_domains: int = Field(0, description="清理前 cookie 覆盖的站点数（供前端提示影响范围）")
    message: str = Field("", description="结果说明")


# ============ 浏览器信息响应 ============


class BrowserInfoResponse(SQLModel):
    """浏览器信息响应"""

    browser_id: str = Field(..., description="浏览器实例ID")
    mid: int = Field(..., description="用户ID")
    user_data_dir: str | None = Field(None, description="用户数据目录")
    is_headless: bool = Field(False, description="是否为无头模式")
    browser_type: str = Field("chromium", description="浏览器类型")
    version: str = Field("", description="浏览器版本")
    user_agent: str = Field("", description="User-Agent")


__all__ = [
    "OpenPageRequest",
    "ClosePageRequest",
    "SwitchPageRequest",
    "GetPageInfoRequest",
    "ClearLoginStateRequest",
    "ClearLoginStateResponse",
    "BrowserInfoResponse",
]
