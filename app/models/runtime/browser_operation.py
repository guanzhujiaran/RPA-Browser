"""
Runtime 模块 - 浏览器控制「操作」接口模型

`/api/v1/rpa/browser/control/operation/*` 这一组**实时操作接口**的请求 / 响应模型。
（RPA action 的参数模型在 `app/models/execution/action_params.py`，与本模块无关。）
"""

import math
from collections.abc import Mapping
from typing import Literal

from pydantic import field_validator, model_validator
from sqlmodel import SQLModel, Field

from app.config import settings


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

    domains: list[str] | None = Field(
        None,
        description=(
            "只清空这些站点的登录态；不传或空列表 = 清空全部（换号）。"
            "可传域名（bilibili.com / .bilibili.com）或 URL，带协议/端口/路径会被归一化。"
            "匹配语义是「访问该站点时实际会携带的 cookie」：包含其子域与父域。"
            "⚠️ 权限授权（摄像头 / 通知等）没有按站点清理的能力，指定站点时仍会整体重置"
        ),
    )
    reload_pages: bool = Field(
        True,
        description="清空后是否刷新已打开的页面：不刷新的话页面上仍是旧账号的 DOM，用户会以为没生效",
    )


class ClearLoginStateResponse(SQLModel):
    """清空登录态（换号）响应"""

    cleared_cookies: bool = Field(
        ..., description="是否已清空目标范围内的 cookie（含 HttpOnly）"
    )
    cleared_cookie_count: int = Field(0, description="已清理的 cookie 条数")
    matched_domains: list[str] = Field(
        default_factory=list,
        description="实际清理的 cookie 域名（去重排序；指定站点时为匹配到的域名）",
    )
    # 用「页面数」而不是「是否成功」：多页面时部分失败无法用布尔值表达真实情况
    cleared_pages: int = Field(0, description="已清空 web storage 的页面数")
    reloaded_pages: int = Field(
        0, description="已提交后台刷新的页面数（响应不等待刷新完成）"
    )
    affected_domains: int = Field(
        0, description="清理范围内 cookie 覆盖的站点数（供前端提示影响范围）"
    )
    message: str = Field("", description="结果说明")


# ============ 导出 Cookie（只读） ============


class ExportCookiesRequest(SQLModel):
    """导出 cookie 请求"""

    domains: list[str] | None = Field(
        None,
        description=(
            "只导出这些站点的 cookie；不传或空列表 = 导出全部。"
            "可传域名（bilibili.com / .bilibili.com）或 URL，带协议/端口/路径会被归一化。"
            "匹配语义是「访问该站点时实际会携带的 cookie」：包含其子域与父域"
        ),
    )
    launch_if_not_running: bool = Field(
        False,
        description=(
            "浏览器未运行时是否顺带启动再导出。cookie 只能从运行中的 context 读取，"
            "未启动时默认返回 1007；启动会占启动队列与时长配额，由调用方显式决定"
        ),
    )


#: Playwright 允许的最大 expires（9999-12-31）；超过会直接抛
#: "Cookie should have a valid expires"，整批写入失败。
MAX_COOKIE_EXPIRES = 253402300799


def normalize_cookie_expires(value: float | None) -> float:
    """把任意来源的过期时间归一化成 Playwright 能接受的形式。

    Playwright 只接受 ``-1`` 或 ``0 < expires <= MAX_COOKIE_EXPIRES`` 的秒级时间戳：

    - ``None`` / ``0``（Chrome 扩展里常表示会话 cookie）/ ``< -1`` / NaN / Inf → ``-1``；
    - 毫秒时间戳（13 位，常见于 JS ``Date.now()`` / 部分扩展）→ 除以 1000；
    - 仍然超上限 → ``-1``（当作会话 cookie，避免整批失败）。
    """
    if value is None:
        return -1.0
    if not math.isfinite(value) or value == 0 or value < -1:
        return -1.0
    if value > MAX_COOKIE_EXPIRES:
        if value < MAX_COOKIE_EXPIRES * 1000:
            value = value / 1000
        if value > MAX_COOKIE_EXPIRES:
            return -1.0
    return float(value)


class CookieData(SQLModel):
    """单条 cookie，字段与 Playwright `BrowserContext.cookies()` 返回一一对应"""

    name: str = Field(..., description="cookie 名")
    value: str = Field(..., description="cookie 值（HttpOnly 也会带出，属敏感凭证）")
    domain: str = Field(..., description="作用域域名，可能带前导点（.bilibili.com）")
    path: str = Field("/", description="作用路径")
    expires: float | None = Field(
        -1,
        description=(
            "过期时间（秒级时间戳）；-1 = 会话 cookie，关闭浏览器即失效。"
            "允许 null（扩展导出的会话 cookie 常见），归一化为 -1；"
            "13 位毫秒时间戳会自动换算成秒"
        ),
    )
    httpOnly: bool = Field(False, description="是否 HttpOnly（页面 JS 读不到）")
    secure: bool = Field(False, description="是否仅 HTTPS 下发送")
    sameSite: str = Field("Lax", description="SameSite 策略：Strict / Lax / None")

    @model_validator(mode="before")
    @classmethod
    def _normalize_expires(cls, data: object) -> object:
        """兼容常见导出工具使用的过期字段名。

        只认 ``expires`` 会把带过期时间的持久 cookie 静默降级成会话 cookie
        （``expires=-1``），表现为「导入成功、重启浏览器登录态没了」。
        Chrome 扩展（EditThisCookie / Cookie-Editor 等）常用 ``expirationDate``。
        """
        if not isinstance(data, Mapping) or "expires" in data:
            return data
        for key in ("expirationDate", "expiration_date"):
            value = data.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                return {**dict(data), "expires": float(value)}
        return data

    @field_validator("expires")
    @classmethod
    def _normalize_expires_value(cls, value: float | None) -> float:
        """归一化过期时间，规则见 ``normalize_cookie_expires``。

        ``null`` / ``0`` / ``< -1`` / NaN / Inf / 超上限都会变成会话 cookie ``-1``，
        导入响应里的 ``session_cookie_count`` 会如实告知「重启后会失效」。
        """
        return normalize_cookie_expires(value)


class ExportCookiesResponse(SQLModel):
    """导出 cookie 响应"""

    cookie_count: int = Field(0, description="导出的 cookie 条数")
    matched_domains: list[str] = Field(
        default_factory=list, description="结果实际覆盖的域名（去重排序）"
    )
    cookies: list[CookieData] = Field(
        default_factory=list,
        description="cookie 明细，结构可直接用于 Playwright add_cookies",
    )
    launched: bool = Field(False, description="本次调用是否顺带启动了浏览器")
    message: str = Field("", description="结果说明")


# ============ 导入 Cookie（一次性，写后不持有） ============


class CookieWriteData(SQLModel):
    """一次 cookie 写入的结果（service 层内部流转用）"""

    imported_count: int = Field(0, description="写入的 cookie 条数")
    reloaded_pages: int = Field(
        0, description="已提交后台刷新的页面数（响应不等待刷新完成）"
    )
    session_cookie_count: int = Field(
        0,
        description="其中会话 cookie（expires < 0）的条数：浏览器重启后会被丢弃",
    )


class ImportCookiesRequest(SQLModel):
    """导入 cookie 请求（单个浏览器）"""

    cookies: list[CookieData] = Field(
        ...,
        min_length=1,
        description="要写入的 cookie；name / value / domain 必填，结构同导出结果",
    )
    launch_if_not_running: bool = Field(
        False,
        description=(
            "浏览器未运行时是否顺带启动再导入。默认 False：未启动直接返回 1007，"
            "由调用方决定是否启动（启动会占启动队列与时长配额，不做隐式副作用）"
        ),
    )
    reload_pages: bool = Field(
        True,
        description="导入后是否刷新已打开页面（否则页面上仍是旧账号的 DOM / 未登录态）",
    )


class ImportCookiesResponse(SQLModel):
    """导入 cookie 响应（成功路径；失败按统一业务异常抛出）"""

    browser_id: str = Field(..., description="目标浏览器 id")
    launched: bool = Field(False, description="本次调用是否顺带启动了浏览器")
    imported_count: int = Field(0, description="写入的 cookie 条数")
    reloaded_pages: int = Field(
        0, description="已提交后台刷新的页面数（响应不等待刷新完成）"
    )
    session_cookie_count: int = Field(
        0,
        description=(
            "其中会话 cookie（expires = -1）的条数：Chromium 重启时会丢弃，"
            "前端应提示「这 N 条重启后失效」"
        ),
    )
    message: str = Field("", description="结果说明")


CookieImportStatus = Literal[
    "success",
    "not_running",
    "workflow_running",
    "forbidden",
    "invalid",
    "launch_failed",
    "injection_failed",
    "unassigned",
]


class ImportCookiesBatchRequest(SQLModel):
    """批量导入 cookie 请求

    按索引配对：``cookie_groups[i]`` 写入 ``browser_ids[i]``。
    两侧数量不一致时「尽力导入」，不整体报错：

    - 组数多于浏览器 → 多出的组整体忽略，响应给出未使用的组数与 cookie 条数；
    - 浏览器多于组数 → 多出的浏览器标记为 ``unassigned``，不写入任何 cookie。
    """

    browser_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=settings.cookie_batch_max_browsers,
        description="按顺序参与导入的浏览器 id（必须是当前用户自己的浏览器）",
    )
    cookie_groups: list[list[CookieData]] = Field(
        ...,
        min_length=1,
        description="第 i 组写入第 i 个浏览器；每组至少 1 条 cookie",
    )
    launch_if_not_running: bool = Field(
        False, description="批次内浏览器未运行时是否顺带启动（逐项串行，失败不影响其它项）"
    )
    reload_pages: bool = Field(True, description="导入后是否刷新已打开页面")

    @field_validator("cookie_groups")
    @classmethod
    def _reject_empty_group(
        cls, groups: list[list[CookieData]]
    ) -> list[list[CookieData]]:
        """空组没有意义，且会静默「占位」导致后面的组整体错位，必须提前拒绝。"""
        for index, group in enumerate(groups, start=1):
            if not group:
                raise ValueError(f"第 {index} 组 cookie 为空")
        return groups


class ImportCookiesBatchItemResult(SQLModel):
    """批量导入中单个浏览器的执行结果"""

    browser_id: str = Field(..., description="目标浏览器 id")
    status: CookieImportStatus = Field(..., description="单项状态")
    launched: bool = Field(False, description="该项是否顺带启动了浏览器")
    imported_count: int = Field(0, description="写入的 cookie 条数")
    reloaded_pages: int = Field(
        0, description="已提交后台刷新的页面数（响应不等待刷新完成）"
    )
    session_cookie_count: int = Field(
        0, description="其中会话 cookie（expires = -1）的条数：浏览器重启后失效"
    )
    message: str = Field("", description="该项结果说明")


class ImportCookiesBatchResponse(SQLModel):
    """批量导入 cookie 响应（逐项状态，不因单项失败整体失败）"""

    total_browsers: int = Field(0, description="提交的浏览器数")
    assigned: int = Field(
        0, description="实际分配到 cookie 的浏览器数（= 浏览器数与组数的较小值）"
    )
    succeeded: int = Field(0, description="已分配项中成功的数量")
    failed: int = Field(0, description="已分配项中失败的数量")
    unassigned_browser_ids: list[str] = Field(
        default_factory=list, description="分不到 cookie 的浏览器（组数不足）"
    )
    unused_cookie_group_count: int = Field(
        0, description="多出而未使用的 cookie 组数（浏览器数不足）"
    )
    unused_cookie_count: int = Field(
        0, description="未使用的 cookie 总条数（多出各组的条数之和）"
    )
    results: list[ImportCookiesBatchItemResult] = Field(
        default_factory=list,
        description="逐项结果：已分配项按配对顺序在前，unassigned 项在后",
    )


# ============ 批量导出 Cookie（只读） ============


CookieExportStatus = Literal[
    "success",
    "not_running",
    "forbidden",
    "invalid",
    "launch_failed",
    "failed",
]


class ExportCookiesBatchRequest(SQLModel):
    """批量导出 cookie 请求

    逐项只读，顺序与响应 ``results`` 一一对应。只处理**运行中**的浏览器：
    cookie 的唯一可读来源是活跃 ``BrowserContext``（profile 里的 SQLite 是加密的），
    未启动项返回 ``not_running``，本接口不提供「顺带启动」。
    """

    browser_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=settings.cookie_batch_max_browsers,
        description="目标浏览器 id（必须是当前用户自己的浏览器）",
    )
    domains: list[str] | None = Field(
        None,
        description="站点过滤，语义与单浏览器导出完全一致；不传或空列表 = 导出全部",
    )


class ExportCookiesBatchItemResult(SQLModel):
    """批量导出中单个浏览器的执行结果"""

    browser_id: str = Field(..., description="目标浏览器 id")
    status: CookieExportStatus = Field(..., description="单项状态")
    launched: bool = Field(False, description="该项是否顺带启动了浏览器")
    cookie_count: int = Field(0, description="该项导出的 cookie 条数")
    matched_domains: list[str] = Field(
        default_factory=list, description="该项实际覆盖的域名（去重排序）"
    )
    cookies: list[CookieData] = Field(
        default_factory=list,
        description="cookie 明细，结构同单浏览器导出；非 success 时为空",
    )
    message: str = Field("", description="该项结果说明")


class ExportCookiesBatchResponse(SQLModel):
    """批量导出 cookie 响应（逐项状态，不因单项失败整体失败）"""

    total_browsers: int = Field(0, description="提交的浏览器数")
    succeeded: int = Field(0, description="导出成功的浏览器数")
    failed: int = Field(0, description="未成功导出的浏览器数（未启动 / 无权 / 失败）")
    total_cookie_count: int = Field(0, description="所有成功项导出的 cookie 总条数")
    results: list[ExportCookiesBatchItemResult] = Field(
        default_factory=list, description="逐项结果，顺序与请求 browser_ids 一致"
    )


# ============ 批量清空登录态（换号） ============


CookieClearStatus = Literal[
    "success",
    "not_running",
    "workflow_running",
    "forbidden",
    "invalid",
    "failed",
]


class ClearLoginStateBatchRequest(SQLModel):
    """批量清空登录态请求（语义与单浏览器 ``clear_login_state`` 完全一致）"""

    browser_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=settings.cookie_batch_max_browsers,
        description="目标浏览器 id（必须是当前用户自己的浏览器）",
    )
    domains: list[str] | None = Field(
        None,
        description=(
            "只清空这些站点；不传或空列表 = 清空全部。匹配语义与单浏览器清空一致，"
            "非法域名属请求级参数错误（整批 400）"
        ),
    )
    reload_pages: bool = Field(
        True,
        description=(
            "清空后是否刷新已打开页面：不刷新的话页面上仍是旧账号的 DOM，"
            "用户会以为没生效"
        ),
    )


class ClearLoginStateBatchItemResult(SQLModel):
    """批量清空中单个浏览器的执行结果"""

    browser_id: str = Field(..., description="目标浏览器 id")
    status: CookieClearStatus = Field(..., description="单项状态")
    cleared_cookie_count: int = Field(0, description="已清理的 cookie 条数")
    matched_domains: list[str] = Field(
        default_factory=list, description="实际清理的 cookie 域名（去重排序）"
    )
    cleared_pages: int = Field(0, description="已清空 web storage 的页面数")
    reloaded_pages: int = Field(
        0, description="已提交后台刷新的页面数（响应不等待刷新完成）"
    )
    affected_domains: int = Field(
        0, description="清理范围内 cookie 覆盖的站点数（供前端提示影响范围）"
    )
    message: str = Field("", description="该项结果说明")


class ClearLoginStateBatchResponse(SQLModel):
    """批量清空登录态响应（逐项状态，不因单项失败整体失败）"""

    total_browsers: int = Field(0, description="提交的浏览器数")
    succeeded: int = Field(0, description="清空成功的浏览器数")
    failed: int = Field(
        0, description="未成功清空的浏览器数（未启动 / 工作流执行中 / 无权 / 失败）"
    )
    total_affected_domains: int = Field(
        0, description="成功项清理前覆盖的站点数之和（供前端提示影响范围）"
    )
    total_cleared_cookie_count: int = Field(
        0, description="成功项清理的 cookie 总条数"
    )
    results: list[ClearLoginStateBatchItemResult] = Field(
        default_factory=list, description="逐项结果，顺序与请求 browser_ids 一致"
    )


# ============ 浏览器信息响应 ============


class BrowserInfoResponse(SQLModel):
    """浏览器信息响应"""

    browser_id: str = Field(..., description="浏览器实例ID")
    mid: int = Field(..., description="用户ID")
    user_data_dir: str | None = Field(None, description="用户数据目录")
    is_headless: bool = Field(False, description="是否为无头模式")
    browser_type: str = Field("chromium", description="浏览器类型")
    version: str = Field("", description="模拟版本（下发给页面的报告版本）")
    real_version: str = Field("", description="真实版本（实际启动的 chromium 二进制版本）")
    user_agent: str = Field("", description="User-Agent")


__all__ = [
    "OpenPageRequest",
    "ClosePageRequest",
    "SwitchPageRequest",
    "GetPageInfoRequest",
    "ClearLoginStateRequest",
    "ClearLoginStateResponse",
    "ExportCookiesRequest",
    "CookieData",
    "ExportCookiesResponse",
    "CookieWriteData",
    "ImportCookiesRequest",
    "ImportCookiesResponse",
    "CookieImportStatus",
    "ImportCookiesBatchRequest",
    "ImportCookiesBatchItemResult",
    "ImportCookiesBatchResponse",
    "CookieExportStatus",
    "ExportCookiesBatchRequest",
    "ExportCookiesBatchItemResult",
    "ExportCookiesBatchResponse",
    "CookieClearStatus",
    "ClearLoginStateBatchRequest",
    "ClearLoginStateBatchItemResult",
    "ClearLoginStateBatchResponse",
    "BrowserInfoResponse",
]
