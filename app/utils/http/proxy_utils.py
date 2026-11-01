"""代理配置归一化工具。

botright 的 ``ProxyManager`` 只接受 ``host:port``、``user:pass@host:port`` 这类形式，
带协议前缀（如 ``http://127.0.0.1:3128``）会被判为非法格式并抛 ``SplitError``；
而项目配置/环境变量里习惯写完整的 URL。统一在这里归一化，避免启动浏览器时才炸。

同时提供两件事：

1. 「未指定代理时回退到项目默认代理」的解析入口，
   保证浏览器实际使用的代理与配置中心（``settings.default_proxy_server``）一致；
2. 代理绕过列表（``bypass``）的归一化与解析，对应 Chromium 的
   ``--proxy-bypass-list``：命中的主机直连、不走代理。
"""

from app.config import settings

# 常见代理协议前缀；长前缀在前，避免 "socks://" 抢先匹配 "socks5://"
_PROXY_SCHEMES: tuple[str, ...] = (
    "socks5h://",
    "socks5://",
    "socks4a://",
    "socks4://",
    "socks://",
    "https://",
    "http://",
)

# 绕过规则里的分隔符：playwright 的 ``proxy.bypass`` 用逗号，Chromium 的
# ``--proxy-bypass-list`` 用分号，用户还可能从浏览器参数里直接粘贴过来。
_BYPASS_SEPARATORS: tuple[str, ...] = (";", "\n", "\t", " ")


def normalize_proxy_server(proxy_server: str | None) -> str | None:
    """把代理配置归一化成 botright 可解析的形式。

    Args:
        proxy_server: 原始代理配置，可能带协议前缀，也可能是空串/空白。

    Returns:
        去掉协议前缀与结尾斜杠后的代理串；空值返回 ``None``（表示不配置代理）。
    """
    if not proxy_server:
        return None

    value = proxy_server.strip()
    if not value:
        return None

    lowered = value.lower()
    for scheme in _PROXY_SCHEMES:
        if lowered.startswith(scheme):
            value = value[len(scheme) :]
            break

    value = value.rstrip("/").strip()
    return value or None


def normalize_proxy_bypass(proxy_bypass: str | None) -> str | None:
    """把代理绕过列表归一化成 playwright ``proxy.bypass`` 要求的逗号分隔形式。

    支持用户按 Chromium 的习惯传分号分隔（``a.com;b.com``）、带空格或换行，
    统一转成逗号分隔并去重，其余规则原样保留（``*.example.com``、``.example.com``、
    ``127.0.0.1``、``10.0.0.0/8``、``<local>``、``<-loopback>`` 等）。

    Args:
        proxy_bypass: 原始绕过列表，空串/空白视为未配置。

    Returns:
        逗号分隔的绕过规则；空值返回 ``None``。

    Raises:
        ValueError: 规则里出现协议前缀（如 ``http://example.com``）或带路径的 URL。
            绕过列表只按 host 匹配，写完整 URL 会静默不生效，因此在入参处直接拦截。
    """
    if not proxy_bypass:
        return None

    value = proxy_bypass
    for separator in _BYPASS_SEPARATORS:
        value = value.replace(separator, ",")

    rules: list[str] = []
    for raw_rule in value.split(","):
        rule = raw_rule.strip()
        if not rule:
            continue
        if "://" in rule:
            raise ValueError(
                f"代理绕过规则只支持主机名/IP，不能带协议前缀: {rule!r}"
                "（应写 *.example.com，而不是 https://example.com）"
            )
        if rule not in rules:
            rules.append(rule)

    return ",".join(rules) or None


def resolve_browser_proxy_server(proxy_server: str | None) -> str | None:
    """解析浏览器实际使用的代理。

    优先级：指纹（浏览器实例）自带的 ``proxy_server`` → 项目默认代理
    ``settings.default_proxy_server``。两者都为空则返回 ``None``（直连）。

    Args:
        proxy_server: 浏览器实例上保存的代理配置。

    Returns:
        归一化后的代理串；未配置代理时返回 ``None``。
    """
    return normalize_proxy_server(proxy_server) or normalize_proxy_server(
        settings.default_proxy_server
    )


def resolve_browser_proxy_bypass(proxy_bypass: str | None) -> str | None:
    """解析浏览器实际使用的代理绕过列表。

    优先级：浏览器实例（用户为这个浏览器单独配置的）``proxy_bypass`` →
    项目默认 ``settings.browser_proxy_bypass``（环境变量兜底）。
    两者都为空则返回 ``None``（不绕过任何主机）。

    Args:
        proxy_bypass: 浏览器实例上保存的绕过列表。

    Returns:
        逗号分隔的绕过规则；未配置时返回 ``None``。
    """
    return normalize_proxy_bypass(proxy_bypass) or normalize_proxy_bypass(
        settings.browser_proxy_bypass
    )


__all__ = [
    "normalize_proxy_bypass",
    "normalize_proxy_server",
    "resolve_browser_proxy_bypass",
    "resolve_browser_proxy_server",
]
