"""客户端上下文（IP / 属地 / 设备）—— 观看者列表展示用

来源：

- **IP**：请求头 `x-bili-client-ip`，由**网关**解析前置 nginx 传来的
  `X-Real-IP` / `X-Forwarded-For` 后注入（见 be-gateway
  `ProxyHelper.resolveClientIp` + `setUserHeaders`）。
  RPA 服务看到的连接来源是网关自己（同机即 `127.0.0.1`），拿不到真实客户端，
  因此必须依赖该头。头缺失时为空串，前端回退显示「未知来源」。
- **User-Agent**：浏览器直接发送，经 nginx / 网关照常转发，可直接读取；用于
  归类出设备串（`Windows · Chrome 126`）、设备类型（`desktop` / `mobile` / `tablet`）
  与浏览器大版本。
- **IP 属地 / 运营商**：不在本服务解析 —— 走 be-message 的 GeoIP RPC
  （`message.geoip.rpc.resolve_ip_region`，City 库出属地、ASN 库出运营商，
  mmdb 单一来源，见 `app.services.mq.rpc_geoip`），失败一律降级为空串。

⚠️ 以上都是**展示信息**（供浏览器归属者判断「是谁在看」），
缺失或不可信都不应阻塞建流，因此本模块不抛错。

详见 docs/rpa-多观看者并发直播计划书.md §2.7。
"""

from __future__ import annotations

import re

from fastapi import Header
from pydantic import BaseModel, Field

from app.services.mq.rpc_geoip import resolve_ip_profile

# 设备类型稳定码（前端按码出 i18n 文案；空串 = 识别不出）
DEVICE_TYPE_DESKTOP = "desktop"
DEVICE_TYPE_MOBILE = "mobile"
DEVICE_TYPE_TABLET = "tablet"

# 浏览器识别顺序**不可调换**：Edge 的 UA 含 `chrome`、Chrome 的 UA 含 `safari`，
# 先命中的才是真实浏览器；括号内为该浏览器的版本号位置（取大版本）。
# ⚠️ 正则统一小写：匹配前 UA 已被 `lower()`，大写模式会永远匹配不上。
_BROWSER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Edge", re.compile(r"(?:edg|edge)/(\d+)")),
    ("Firefox", re.compile(r"firefox/(\d+)")),
    ("Chrome", re.compile(r"(?:chrome|chromium)/(\d+)")),
    # Safari 的 UA 里 `Safari/605.1.15` 是 WebKit 版本，真实版本在 `Version/17.5`
    ("Safari", re.compile(r"version/(\d+)")),
)


class UserAgentInfo(BaseModel):
    """User-Agent 的粗粒度结构化结果"""

    system: str = Field("", description="操作系统，如 Windows / macOS / Android")
    browser: str = Field("", description="浏览器，如 Chrome / Edge / Safari")
    browser_version: str = Field("", description="浏览器大版本，如 126")
    device_type: str = Field(
        "",
        description="设备类型稳定码：desktop / mobile / tablet；识别不出为空串",
    )
    device: str = Field("", description="可读设备串，如「Windows · Chrome 126」")


class ClientContext(BaseModel):
    """一次请求的客户端上下文"""

    ip: str = Field(
        "",
        description=(
            "客户端 IP（网关从 nginx 的 X-Real-IP / X-Forwarded-For 解析后注入；"
            "取不到为空串）"
        ),
    )
    user_agent: str = Field("", description="原始 User-Agent")
    device: str = Field("", description="可读设备描述，如「Windows · Chrome 126」")
    device_type: str = Field(
        "", description="设备类型稳定码：desktop / mobile / tablet（空串=识别不出）"
    )
    browser_version: str = Field("", description="浏览器大版本，如 126")
    ip_region: str = Field(
        "",
        description="IP 属地，如「浙江 杭州」（be-message GeoIP RPC 解析；失败为空串）",
    )
    ip_isp: str = Field(
        "",
        description=(
            "IP 运营商（ASN 组织名，**英文**，如 `China Unicom Shanghai network`；"
            "与属地同一次 RPC 一并返回；失败为空串）"
        ),
    )


def parse_user_agent(user_agent: str) -> UserAgentInfo:
    """把 User-Agent 粗粒度解析为系统 / 浏览器 / 大版本 / 设备类型。

    只做粗粒度识别（够用于判断「是谁在看」），刻意不引入 ua-parser 之类的依赖。
    识别不出来时返回空串字段，由前端回退为「未知设备」。

    ⚠️ 判断顺序不可调换：
    - Android 的 UA 里含 `linux`，iPad 的 UA 里含 `mac os`，故系统判断 Android/iPad 在前；
    - 浏览器顺序见 `_BROWSER_PATTERNS` 的注释（Edge → Firefox → Chrome → Safari）。
    """
    if not user_agent:
        return UserAgentInfo()
    ua = user_agent.lower()

    if "android" in ua:
        system = "Android"
    elif "iphone" in ua:
        system = "iPhone"
    elif "ipad" in ua:
        system = "iPad"
    elif "windows" in ua:
        system = "Windows"
    elif "mac os" in ua or "macintosh" in ua:
        system = "macOS"
    elif "linux" in ua:
        system = "Linux"
    else:
        system = ""

    browser = ""
    browser_version = ""
    for name, pattern in _BROWSER_PATTERNS:
        matched = pattern.search(ua)
        if matched:
            browser = name
            browser_version = matched.group(1)
            break

    # 设备类型：iPad 与「不带 mobile 的 Android」都是平板；
    # iPhone 与带 mobile 的 UA 是移动端；其余识别出系统的按桌面处理。
    if "ipad" in ua or ("android" in ua and "mobile" not in ua):
        device_type = DEVICE_TYPE_TABLET
    elif "iphone" in ua or "mobile" in ua:
        device_type = DEVICE_TYPE_MOBILE
    elif system:
        device_type = DEVICE_TYPE_DESKTOP
    else:
        device_type = ""

    browser_display = f"{browser} {browser_version}".strip()
    return UserAgentInfo(
        system=system,
        browser=browser,
        browser_version=browser_version,
        device_type=device_type,
        device=" · ".join(part for part in (system, browser_display) if part),
    )


def describe_device(user_agent: str) -> str:
    """把 User-Agent 归类成「系统 · 浏览器 大版本」（`parse_user_agent` 的展示串）"""
    return parse_user_agent(user_agent).device


async def get_client_context(
    x_bili_client_ip: str | None = Header(default=None, alias="x-bili-client-ip"),
    user_agent: str | None = Header(default=None, alias="user-agent"),
) -> ClientContext:
    """从请求头提取客户端上下文（IP / 属地 / 设备）。

    IP 取网关注入的 `x-bili-client-ip`；User-Agent 由浏览器直接发送。
    属地与运营商经 be-message 的 GeoIP RPC **一次调用**解析（内网 / 失败都返回空串）。
    全部字段都可能缺失，由前端回退显示「未知来源 / 未知属地 / 未知运营商 / 未知设备」。
    **不抛错** —— 展示信息不该阻塞建流。

    ⚠️ 该解析是一次跨服务调用，只在**观看者接入**（offer）时发生，
    不挂在 answer / ICE / 心跳等高频端点上。
    """
    ip = (x_bili_client_ip or "").strip()
    ua = (user_agent or "").strip()
    parsed = parse_user_agent(ua)
    profile = await resolve_ip_profile(ip)
    return ClientContext(
        ip=ip,
        user_agent=ua,
        device=parsed.device,
        device_type=parsed.device_type,
        browser_version=parsed.browser_version,
        ip_region=profile.region,
        ip_isp=profile.isp,
    )


__all__ = [
    "ClientContext",
    "UserAgentInfo",
    "parse_user_agent",
    "describe_device",
    "get_client_context",
]
