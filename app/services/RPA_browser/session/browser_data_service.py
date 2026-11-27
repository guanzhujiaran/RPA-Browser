"""
浏览器运行态数据服务

只处理「会话正在跑、不需要重启浏览器就能复位 / 读取」的数据 —— 目前是登录态：
cookie（含 HttpOnly）+ 权限授权 + 页面 web storage。

⚠️ 刻意不碰 profile 里的磁盘数据（HTTP 缓存 / Service Worker / IndexedDB / 扩展数据）：
清那些必须删掉 `user_data_dir` 再重建浏览器，属于另一条路径，不在本服务范围内。
反之，若站点把凭证存在 IndexedDB / SW 里（不靠 cookie），本服务对它无效。

⚠️ cookie 的唯一真相源是浏览器 profile 本身：
本服务只做「读出来」和「清掉」，**不缓存、不落库、不回灌** —— 站点会随请求轮换
cookie（如 B 站 SESSDATA），程序侧快照必然过期，启动时注入只会用作废凭证
覆盖浏览器刷新后的有效凭证，属于主动劣化。
"""

import asyncio
from collections.abc import Coroutine, Mapping, Sequence
from urllib.parse import urlsplit

from loguru import logger

from botright.playwright_mock import BrowserContext, Page
from playwright._impl._errors import TargetClosedError

from app.models.common.exceptions.base_exception import InvalidParamException
from app.models.runtime.browser_operation import (
    ClearLoginStateResponse,
    CookieData,
    CookieWriteData,
    ExportCookiesResponse,
    normalize_cookie_expires,
)
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    WebRTCEnabledSession,
)


#: sameSite 归一化：外部 JSON 可能给 ``lax`` / ``LAX``，Chromium 只认首字母大写
_SAME_SITE_ALIASES: Mapping[str, str] = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
}

#: 后台任务引用集合：asyncio 只持弱引用，不保存会被 GC 回收，导致刷新静默丢失
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _spawn_background(coro: Coroutine[object, object, None]) -> None:
    """把「不影响接口结果」的页面刷新丢到后台，避免拖长 HTTP 请求。

    背景：`page.reload()` 默认等 load 事件，慢站点 / 多页面叠加时单次导入能跑
    几十秒，超过 nginx 默认 60s 读超时 → 前端 504，但 cookie 其实已经写入，
    用户会误以为失败并重试。
    """
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


class BrowserDataService:
    """浏览器运行态数据（登录态等）清理服务。"""

    #: 清 web storage 的脚本。about:blank / sandbox 页面访问 localStorage 会抛异常，
    #: 故脚本内部再兜一层，避免「这个页面没有 storage」把整页清理打断。
    _CLEAR_WEB_STORAGE_SCRIPT = (
        "() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"
    )

    @staticmethod
    async def count_cookie_domains(browser_context: BrowserContext) -> int:
        """统计 cookie 覆盖的站点数（只读）。

        供前端在二次确认时提示「会退出 N 个站点的登录态」，必须在清理**之前**调用。
        """
        cookies = await browser_context.cookies()
        return len({cookie["domain"] for cookie in cookies})

    # ============ 导出 cookie（只读） ============

    @classmethod
    async def export_cookies(
        cls,
        browser_context: BrowserContext,
        *,
        domains: Sequence[str] | None = None,
    ) -> ExportCookiesResponse:
        """把浏览器**当前**持有的 cookie 原样读出（只读，不落库、不缓存）。

        数据来源只能是 profile：站点会随请求轮换 cookie，任何程序侧快照都会过期，
        所以这里只做一次性读取，不提供任何「导出 → 启动时回灌」的链路。

        Args:
            browser_context: 活跃会话的浏览器上下文
            domains: 站点过滤，不传 / 空 = 全部。语义是「访问这些站点时实际会携带的
                cookie」：与目标站点相等、是其子域、或是其父域都算
                （``.bilibili.com`` 的 cookie 在访问 ``www.bilibili.com`` 时同样生效，
                漏掉它导出的登录态就不完整）。可传域名或 URL。

        Returns:
            ExportCookiesResponse: 明细 + 覆盖域名 + 条数

        Raises:
            InvalidParamException: 传了 domains 但一个有效域名都解析不出来
                （不能退化成「导出全部」，否则会静默放大导出范围）
        """
        raw_cookies = await browser_context.cookies()

        sites = cls._resolve_sites(domains)
        selected = [
            raw
            for raw in raw_cookies
            if not sites
            or cls._cookie_matches_sites(str(raw.get("domain", "")), sites)
        ]

        items = [cls._build_cookie_item(raw) for raw in selected]
        # 排序只为输出稳定：Playwright 不保证返回顺序
        items.sort(key=lambda item: (item.domain, item.path, item.name))
        matched_domains = sorted({item.domain for item in items})

        return ExportCookiesResponse(
            cookie_count=len(items),
            matched_domains=matched_domains,
            cookies=items,
            message=(
                f"已导出 {len(items)} 条 cookie，覆盖 {len(matched_domains)} 个域名"
                if items
                else "没有匹配到 cookie（可能尚未登录，或过滤域名不匹配）"
            ),
        )

    @classmethod
    def validate_domains(cls, domains: Sequence[str] | None) -> None:
        """校验站点过滤条件（只校验、不导出）。

        供批量接口在循环前**一次性**校验：``domains`` 非法属于请求级参数错误，
        不能退化成逐项 ``invalid`` —— 那既放大响应体，也会让用户误以为是个别
        浏览器的问题。
        """
        cls._resolve_sites(domains)

    @classmethod
    def _resolve_sites(cls, domains: Sequence[str] | None) -> tuple[str, ...]:
        """归一化站点过滤条件；全部解析失败时抛异常而不是退化成「导出全部」。"""
        if not domains:
            return ()
        sites = tuple(
            site
            for site in (cls._normalize_site(domain) for domain in domains)
            if site
        )
        if not sites:
            raise InvalidParamException(
                f"domains 无法解析出有效域名: {list(domains)!r}"
            )
        return sites

    @staticmethod
    def _normalize_site(site: str) -> str:
        """把用户输入的域名 / URL 归一成可比较的主机名。

        容忍误传协议、端口、路径：``https://www.bilibili.com/x`` → ``www.bilibili.com``。
        """
        value = site.strip().lower()
        if "://" in value:
            return (urlsplit(value).hostname or "").strip(".")
        for sep in ("/", "?", "#"):
            value = value.split(sep, 1)[0]
        # 只剥「冒号后全是数字」的端口，避免把 IPv6 字面量切坏（[::1]:8080）
        head, colon, tail = value.rpartition(":")
        if colon and tail.isdigit():
            value = head
        return value.strip(".").strip("[]")

    @staticmethod
    def _cookie_matches_sites(cookie_domain: str, sites: tuple[str, ...]) -> bool:
        """cookie 作用域是否与任一目标站点相关（相等 / 子域 / 父域）。"""
        host = cookie_domain.strip().lower().strip(".")
        if not host:
            return False
        return any(
            host == site or host.endswith(f".{site}") or site.endswith(f".{host}")
            for site in sites
        )

    @staticmethod
    def _build_cookie_item(raw: Mapping[str, str | float | bool]) -> CookieData:
        """把 Playwright 的 cookie 字典转成严格模型，缺失字段按浏览器默认值兜底。"""
        raw_expires = raw.get("expires", -1)
        return CookieData(
            name=str(raw.get("name", "")),
            value=str(raw.get("value", "")),
            domain=str(raw.get("domain", "")),
            path=str(raw.get("path", "/")),
            expires=(
                float(raw_expires)
                if isinstance(raw_expires, int | float)
                else -1.0
            ),
            httpOnly=bool(raw.get("httpOnly", False)),
            secure=bool(raw.get("secure", False)),
            sameSite=str(raw.get("sameSite", "Lax")),
        )

    # ============ 导入 cookie（一次性写入，写后不持有） ============

    @classmethod
    async def import_cookies(
        cls,
        browser_context: BrowserContext,
        cookies: Sequence[CookieData],
        *,
        reload_pages: bool = True,
    ) -> CookieWriteData:
        """把 cookie 写入**运行中**的浏览器上下文，写后服务端不再持有任何副本。

        必须在运行态：``add_cookies`` 是唯一有序的注入点，它写入浏览器内存、
        由浏览器自己决定落盘时机。离线改 profile 的 Cookies SQLite 会与浏览器
        自身的合并逻辑打架，且磁盘数据本身是滞后快照。

        加载语义（不是「替换」）：
        - 同 ``domain + path + name`` 覆盖旧值（upsert），不会产生重复条目；
        - 本次没给的旧 cookie 保留 —— 要彻底换号请先调 ``clear_login_state``；
        - 写入后由浏览器负责后续刷新与落盘，服务端不缓存。

        Args:
            browser_context: 活跃会话的浏览器上下文
            cookies: 待写入的 cookie
            reload_pages: 是否刷新已打开页面，让站点立即看到新的登录态

        Returns:
            CookieWriteData: 写入条数 + 刷新页面数

        Raises:
            InvalidParamException: 存在缺少 name / domain 的 cookie（整批拒绝，
                避免调用方以为「写入成功」而实际只有部分生效）
        """
        payload = [cls._to_playwright_cookie(cookie) for cookie in cookies]
        await browser_context.add_cookies(payload)  # type: ignore[arg-type]

        # 刷新转后台：reload 会等 load 事件，放在请求路径里会撞网关读超时（nginx 默认 60s）
        pages = list(browser_context.pages) if reload_pages else []
        if pages:
            _spawn_background(cls._reload_pages(pages))
        reloaded = len(pages)

        # 会话 cookie 会被 Chromium 接受并临时落盘，但重启时丢弃 —— 必须让调用方知道
        session_cookie_count = sum(1 for cookie in cookies if cookie.expires < 0)

        return CookieWriteData(
            imported_count=len(payload),
            reloaded_pages=reloaded,
            session_cookie_count=session_cookie_count,
        )

    @classmethod
    def _to_playwright_cookie(
        cls, cookie: CookieData
    ) -> Mapping[str, str | float | bool]:
        """转成 Playwright ``add_cookies`` 需要的结构，并做最小必要归一化。"""
        name = cookie.name.strip()
        domain = cls._normalize_cookie_domain(cookie.domain)
        if not name or not domain:
            raise InvalidParamException(
                f"cookie 缺少 name / domain: "
                f"name={cookie.name!r} domain={cookie.domain!r}"
            )

        same_site = _SAME_SITE_ALIASES.get(cookie.sameSite.strip().lower(), "Lax")
        # 防御式归一化：即使某个入口漏过模型校验，也不能把非法 expires 传给
        # Playwright —— 它的 assert 会让整批 add_cookies 直接失败
        expires = normalize_cookie_expires(cookie.expires)
        if expires != cookie.expires:
            logger.warning(
                f"cookie expires 已归一化: name={cookie.name} "
                f"{cookie.expires} -> {expires}"
            )
        return {
            "name": name,
            "value": cookie.value,
            "domain": domain,
            "path": cookie.path or "/",
            # -1 = 会话 cookie，Chromium 接受该约定
            "expires": expires,
            "httpOnly": cookie.httpOnly,
            # sameSite=None 必须配 secure，否则 Chromium 直接拒收该条
            "secure": cookie.secure or same_site == "None",
            "sameSite": same_site,
        }

    @staticmethod
    def _normalize_cookie_domain(domain: str) -> str:
        """清洗 cookie 域：去协议 / 端口 / 路径，**保留前导点**。

        前导点（``.bilibili.com``）有语义：表示该 cookie 对子域同样生效，
        与导出过滤时用的「归一主机名」不同，这里不能 strip 掉。
        """
        value = domain.strip()
        if "://" in value:
            return (urlsplit(value).hostname or "").lower()
        for sep in ("/", "?", "#"):
            value = value.split(sep, 1)[0]
        head, colon, tail = value.rpartition(":")
        if colon and tail.isdigit():
            value = head
        return value.lower().rstrip(".")

    # ============ 清空登录态（换号） ============

    @classmethod
    async def clear_login_state(
        cls,
        browser_session: WebRTCEnabledSession,
        *,
        domains: Sequence[str] | None = None,
        reload_pages: bool = True,
    ) -> ClearLoginStateResponse:
        """清空登录态：cookie + 权限 + 页面 web storage，可选刷新页面。

        ``domains`` 为空 = 清空全部（换号）；指定站点时只清「访问这些站点会携带的
        cookie」以及 origin 匹配页面的 web storage。

        ⚠️ 权限授权没有按站点清理的能力（Playwright 只有整体 ``clear_permissions``），
        指定站点时仍会整体重置 —— 宁可多清，也不能留下越权残留，调用方需在提示里说明。

        顺序：先 cookie / 权限（登录态的真正载体），再逐页清 web storage，
        最后按需刷新 —— 刷新放在最后，站点重新加载时看到的就是未登录态。

        Args:
            browser_session: 活跃会话实体（由调用方保证会话存在、活跃时间已刷新）
            domains: 站点过滤；``None`` / 空 = 全部
            reload_pages: 是否刷新已打开页面。不刷新的话页面上仍是旧账号的 DOM，
                用户会以为操作没生效

        Returns:
            ClearLoginStateResponse: 清理条数 / 域名 / 受影响站点数 / 页面数

        Raises:
            InvalidParamException: 传了 ``domains`` 但一个有效域名都解析不出
                （不能退化成「清空全部」，否则会静默扩大影响范围）
        """
        browser_context = browser_session.browser_context
        sites = cls._resolve_sites(domains)

        # 先读再清：清完既统计不了影响范围，也拿不到要按哪些 domain 清
        raw_cookies = await browser_context.cookies()
        if sites:
            matched = [
                raw
                for raw in raw_cookies
                if cls._cookie_matches_sites(str(raw.get("domain", "")), sites)
            ]
        else:
            matched = raw_cookies

        matched_domains = sorted(
            {str(raw.get("domain", "")) for raw in matched if raw.get("domain")}
        )

        if sites:
            # Playwright 支持按 domain 精确清空，不做「读-清-回写」（回写会改写 cookie 元数据）
            for domain in matched_domains:
                await browser_context.clear_cookies(domain=domain)
        else:
            await browser_context.clear_cookies()

        await browser_context.clear_permissions()

        cleared_pages = 0
        pages_to_reload: list[Page] = []
        for page in browser_session.all_pages:
            if not cls._page_in_scope(page.url, sites):
                continue
            if await cls._clear_page_web_storage(page):
                cleared_pages += 1
            if reload_pages:
                pages_to_reload.append(page)

        # 刷新转后台：同导入，避免接口被 reload 拖到网关读超时
        if pages_to_reload:
            _spawn_background(cls._reload_pages(pages_to_reload))
        reloaded_pages = len(pages_to_reload)

        if sites:
            scope = "、".join(matched_domains) if matched_domains else "无匹配域名"
            message = (
                f"已清理 {len(matched)} 条 cookie（{scope}），浏览器未重启，可登录新账号；"
                f"权限授权已整体重置（无法按站点保留）"
            )
        else:
            message = (
                f"已退出登录（影响 {len(matched_domains)} 个站点），"
                f"浏览器未重启，可登录新账号"
            )

        return ClearLoginStateResponse(
            cleared_cookies=True,
            cleared_cookie_count=len(matched),
            matched_domains=matched_domains,
            cleared_pages=cleared_pages,
            reloaded_pages=reloaded_pages,
            affected_domains=len(matched_domains),
            message=message,
        )

    @classmethod
    def _page_in_scope(cls, url: str, sites: tuple[str, ...]) -> bool:
        """指定站点时只处理 origin 匹配的页面；about:blank 等无 host 页面不在范围内。"""
        if not sites:
            return True
        try:
            host = urlsplit(url).hostname
        except ValueError:
            return False
        if not host:
            return False
        return cls._cookie_matches_sites(host.lower().rstrip("."), sites)

    @classmethod
    async def _clear_page_web_storage(cls, page: Page) -> bool:
        """清单个页面的 localStorage / sessionStorage。

        单页失败（about:blank / sandbox）只跳过该页，不能让整次清理失败。
        """
        try:
            await page.evaluate(cls._CLEAR_WEB_STORAGE_SCRIPT)
            return True
        except Exception as e:
            logger.debug(f"清理页面 web storage 跳过: {e}")
            return False

    @staticmethod
    async def _reload_pages(pages: Sequence[Page]) -> None:
        """逐个刷新页面（后台执行）。

        失败只记 debug：cookie 已写入 / 清空才是接口的目标，刷新只是让页面立即
        反映新状态；页面被关闭（``TargetClosedError``）直接结束，不影响其它页。
        """
        for page in pages:
            try:
                await page.reload(timeout=15_000)
            except TargetClosedError:
                return
            except Exception as e:
                logger.debug(f"后台刷新页面失败（忽略）: {e}")


__all__ = ["BrowserDataService"]
