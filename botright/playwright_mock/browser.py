from __future__ import annotations

import inspect
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Pattern,
    TypedDict,
    Union,
)
from playwright._impl._async_base import AsyncEventContextManager
from playwright._impl._errors import TargetClosedError
from playwright.async_api import APIResponse
from playwright.async_api import BrowserContext as PlaywrightBrowserContext
from playwright.async_api import ConsoleMessage as PlaywrightConsoleMessage
from playwright.async_api import ElementHandle as PlaywrightElementHandle
from playwright.async_api import Frame as PlaywrightFrame
from playwright.async_api import Page as PlaywrightPage
from playwright.async_api import Request as PlaywrightRequest
from playwright.async_api import Route as PlaywrightRoute

from ..modules import Faker, ProxyManager
from . import ElementHandle, Frame, JSHandle, Page, Request, Route, new_page
from .launch_args import build_launch_args

if TYPE_CHECKING:
    from botright import Botright


def _build_proxy_option(
    proxy: ProxyManager, proxy_bypass: str | None
) -> dict[str, str] | None:
    """组装 playwright 的 ``proxy`` 选项，绕过列表交给 driver 拼 ``--proxy-bypass-list``。

    driver 只认 ``proxy.bypass``（逗号分隔），会自己渲染成
    ``--proxy-bypass-list=a;b;<-loopback>``；在这里手动往 args 塞同名开关会与 driver
    那份重复/互相覆盖，所以绕过列表统一从这里下发。

    没有代理（直连）时绕过列表无意义，且 playwright 要求 ``proxy.server`` 必须存在，
    因此直接返回 ``None``，避免「只配了绕过列表」导致启动报错。
    """
    if not proxy.browser_proxy:
        return None
    if not proxy_bypass:
        return proxy.browser_proxy
    return {**proxy.browser_proxy, "bypass": proxy_bypass}


async def new_browser(
    botright: Botright,
    proxy: ProxyManager,
    faker: Faker,
    flags: List[str],
    proxy_bypass: str | None = None,
    **launch_arguments,
) -> BrowserContext:
    """
    Create a new browser context with custom configurations.

    Args:
        botright (Botright): The Botright Instance from the main thread.
        proxy (ProxyManager): An instance of ProxyManager for configuring proxy settings.
        faker (Faker): An instance of Faker for generating fake user agent and other details.
        flags (List[str]): The command line flags to pass into the Browser Instance.
        proxy_bypass (str | None): Comma-separated hosts that must bypass the proxy.
        # browser_path (str): The path to the real browser executable.
        # headless (bool, optional): Whether to run the browser in headless mode. Defaults to False.
        # mask_fingerprint (bool, optional): Whether to mask the browser fingerprint. Defaults to True.
        # user_action_layer (bool, optional): Whether to enable user action simulation layer. Defaults to False.
        # scroll_into_view (bool, optional): Whether to scroll elements into view automatically. Defaults to True.
        **launch_arguments: Additional launch arguments for configuring the browser context. See https://playwright.dev/python/docs/api/class-browser#browser-new-context.

    Returns:
        BrowserContext: A new browser context with the specified configurations.
    """
    # flags 由内置 flags + 指纹参数 + UA 多来源拼装，这里统一收敛：
    # 每个开关只设置一次，driver 会重复注入的同名开关改为由 ignore_default_args 抑制 driver 那份
    args, ignore_default_args = build_launch_args(
        flags,
        headless=bool(botright.headless),
        use_playwright_proxy=bool(proxy.browser_proxy),
    )
    proxy_option = _build_proxy_option(proxy, proxy_bypass)

    if botright.mask_fingerprint:
        fingerprint = faker.fingerprint
        parsed_launch_arguments = {
            "locale": "en-US",
            "user_agent": fingerprint.navigator.userAgent,
            "timezone_id": proxy.timezone,
            "geolocation": {
                "longitude": proxy.longitude,
                "latitude": proxy.latitude,
                "accuracy": 0.7,
            },
            "permissions": ["geolocation"],
            "ignore_https_errors": True,
            "screen": {
                "width": fingerprint.screen.width,
                "height": fingerprint.screen.height,
            },
            "viewport": {
                "width": fingerprint.screen.availWidth,
                "height": fingerprint.screen.availHeight,
            },
            "color_scheme": "dark",
            "proxy": proxy_option,
            "http_credentials": {"username": proxy.username, "password": proxy.password}
            if proxy.username
            else None,
            "ignore_default_args": ignore_default_args,
            **launch_arguments,
        }  # self.faker.locale
    else:
        parsed_launch_arguments = {
            "locale": "en-US",
            "timezone_id": proxy.timezone,
            "geolocation": {
                "longitude": proxy.longitude,
                "latitude": proxy.latitude,
                "accuracy": 0.7,
            },
            "ignore_https_errors": True,
            "color_scheme": "dark",
            "proxy": proxy_option,
            "http_credentials": {"username": proxy.username, "password": proxy.password}
            if proxy.username
            else None,
            "ignore_default_args": ignore_default_args,
            **launch_arguments,
        }  # self.faker.locale

    _browser = await botright.playwright.chromium.launch_persistent_context(
        headless=botright.headless,
        executable_path=botright.browser["path"],
        args=args,
        chromium_sandbox=True,
        **parsed_launch_arguments,
    )

    browser = BrowserContext(
        _browser,
        proxy,
        faker,
        use_undetected_playwright=botright.use_undetected_playwright,
        cache=botright.cache,
        user_action_layer=botright.user_action_layer,
        mask_fingerprint=botright.mask_fingerprint,
        scroll_into_view=botright.scroll_into_view,
    )

    # Preprocessing to save computing resources
    if botright.block_images:
        await browser.block_images()

    if botright.cache_responses:
        await browser.cache_responses()

    return browser


class BrowserContext(PlaywrightBrowserContext):
    def __init__(
        self,
        browser: PlaywrightBrowserContext,
        proxy: ProxyManager,
        faker: Faker,
        use_undetected_playwright: Optional[bool],
        cache: Dict[str, APIResponse],
        user_action_layer: Optional[bool],
        scroll_into_view: Optional[bool],
        mask_fingerprint: Optional[bool],
    ):
        super().__init__(browser)
        self._impl_obj = browser._impl_obj
        self._browser = browser
        self._closed = False
        self._route_proxies: Dict[
            Union[
                Callable[[PlaywrightRoute], Any],
                Callable[[PlaywrightRoute, PlaywrightRequest], Any],
            ],
            Union[
                Callable[[PlaywrightRoute], Any],
                Callable[[PlaywrightRoute, PlaywrightRequest], Any],
            ],
        ] = {}

        self.proxy = proxy
        self.faker = faker
        self.use_undetected_playwright = use_undetected_playwright

        self.cache = cache
        self.user_action_layer = user_action_layer
        self.scroll_into_view = scroll_into_view
        self.mask_fingerprint = mask_fingerprint

        self._origin_new_page = browser.new_page
        self._origin_close = browser.close
        self._origin_route = browser.route
        self._origin_unroute = browser.unroute
        self._origin_expect_console_message = browser.expect_console_message
        self._origin_expose_function = browser.expose_function
        self._origin_expose_binding = browser.expose_binding

        # Setting first page to be closed as it's the first empty about:blank page of the Persistent Context
        self.persistent_page = self.pages[0]

    def __eq__(self, obj):
        if isinstance(obj, BrowserContext):
            if (
                (obj._browser == self._browser)
                and (obj.proxy == self.proxy)
                and (obj.faker == self.faker)
            ):
                return True
        return False

    @property
    def pages(self):
        if self._closed:
            return []

        pages: List[Page] = []
        for _page in self._browser.pages:
            pages.append(self._wrap_page(_page))
        return pages

    def _wrap_page(self, _page) -> Page:
        """按底层 impl 缓存 Page 包装：同一页面始终返回同一对象。

        没有稳定身份时，``all_pages[i]`` 与 ``new_page()`` 的返回值无法比较，
        上层（如页面别名注册表判断「页面是否被工作流占用」）会全部失效。
        """
        cache = getattr(self, "_page_wrapper_cache", None)
        if cache is None:
            cache = {}
            self._page_wrapper_cache = cache

        impl = getattr(_page, "_impl_obj", None)
        key = id(impl) if impl is not None else id(_page)
        wrapper = cache.get(key)
        if wrapper is None or getattr(wrapper, "_page", None) is not _page:
            wrapper = Page(_page, self, self.faker)
            cache[key] = wrapper
        return wrapper

    async def cache_responses(self):
        async def route_interceptor(route: PlaywrightRoute):
            request = route.request

            if request.resource_type in (
                "document",
                "stylesheet",
                "image",
                "media",
                "font",
                "manifest",
            ):
                if request.url not in self.cache:
                    response = await route.fetch()
                    self.cache[request.url] = response
                else:
                    response = self.cache[request.url]
                await route.fulfill(response=response)
            else:
                await route.continue_()

        await self.route("**", route_interceptor)

    async def block_images(self):
        async def all_blocker(route: PlaywrightRoute):
            await route.abort(error_code="aborted")

        async def image_blocker(route: PlaywrightRoute):
            if route.request.resource_type == "image":
                await route.abort(error_code="aborted")
            else:
                await route.continue_()

        await self.route(
            "**/*.{apng,avif,gif,jpg,jpeg,jfif,pjpeg,pjp,png,svg,webp}", all_blocker
        )
        await self.route("**", image_blocker)

    async def new_page(self, **launch_arguments) -> Page:
        """
        Args:
            **launch_arguments: Dont use! PlaywrightBrowserContext.new_page() doesn't take any args. Only used to prevent errors.

        Returns:
            Page: A new Page instance.
        """
        page = await new_page(self, self.faker)

        # Close first page it's the first empty about:blank page of the Persistent Context
        if not self.persistent_page.is_closed():
            await self.persistent_page.close()

        # 登记进包装缓存，保证后续 pages / all_pages 拿到的是同一对象
        impl = getattr(getattr(page, "_page", None), "_impl_obj", None)
        if impl is not None:
            cache = getattr(self, "_page_wrapper_cache", None)
            if cache is None:
                cache = {}
                self._page_wrapper_cache = cache
            cache[id(impl)] = page

        return page

    async def close(self, reason: Optional[str] = None):
        self._closed = True
        try:
            return await self._origin_close(reason=reason)
        except TargetClosedError:
            return

    async def route(
        self,
        url: Union[str, Pattern[str], Callable[[str], bool]],
        handler: Union[
            Callable[[PlaywrightRoute], Any],
            Callable[[PlaywrightRoute, PlaywrightRequest], Any],
        ],
        times: Optional[int] = None,
    ):
        if (
            len(inspect.signature(handler).parameters) == 2
        ):  # Checking how many parameters the callable expects

            def handler_proxy(route: PlaywrightRoute, request: PlaywrightRequest):
                page = request.frame.page

                route = Route(route, Page(page, self, self.faker))
                request = Request(request, Page(page, self, self.faker))
                return handler(route, request)  # type: ignore

            self._route_proxies[handler] = handler_proxy
            await self._origin_route(url=url, handler=handler_proxy, times=times)
        else:

            def handler_proxy_no_request(route: PlaywrightRoute):
                page = route.request.frame.page

                route = Route(route, Page(page, self, self.faker))
                return handler(route)  # type: ignore

            self._route_proxies[handler] = handler_proxy_no_request
            await self._origin_route(
                url=url, handler=handler_proxy_no_request, times=times
            )

    async def unroute(
        self,
        url: Union[str, Pattern[str], Callable[[str], bool]],
        handler: Optional[
            Union[
                Callable[[PlaywrightRoute], Any],
                Callable[[PlaywrightRoute, PlaywrightRequest], Any],
            ]
        ] = None,
    ):
        if handler:
            handler_proxy = self._route_proxies[handler]
            await self._origin_unroute(url=url, handler=handler_proxy)

        await self._origin_unroute(url=url, handler=None)

    def expect_console_message(
        self,
        predicate: Optional[Callable[..., bool]] = None,
        timeout: Optional[float] = None,
    ) -> AsyncEventContextManager[PlaywrightConsoleMessage]:
        if self.use_undetected_playwright:
            from botright.extended_typing import NotSupportedError

            raise NotSupportedError(
                "BrowserContext.expect_console_message is currently unsupported, due to CDP Runtime Patches."
            )

        return self._origin_expect_console_message(predicate=predicate, timeout=timeout)

    async def expose_function(self, name: str, callback: Callable[..., None]) -> None:
        if self.use_undetected_playwright:
            from botright.extended_typing import NotSupportedError

            raise NotSupportedError(
                "BrowserContext.expose_function is currently unsupported, due to CDP Runtime Patches."
            )

        return await self._origin_expose_function(name=name, callback=callback)

    async def expose_binding(
        self, name: str, callback: Callable[..., None], handle: Optional[bool] = None
    ):
        if self.use_undetected_playwright:
            from botright.extended_typing import NotSupportedError

            raise NotSupportedError(
                "BrowserContext.expose_binding is currently unsupported, due to CDP Runtime Patches."
            )

        class SourceDict(TypedDict):
            context: PlaywrightBrowserContext
            page: PlaywrightPage
            frame: PlaywrightFrame

        if handle:

            def callback_proxy_handle(source: SourceDict, element: Any):
                _context: PlaywrightBrowserContext = source["context"]
                _page: PlaywrightPage = source["page"]
                _frame: PlaywrightFrame = source["frame"]

                context = BrowserContext(
                    _context,
                    self.proxy,
                    self.faker,
                    use_undetected_playwright=self.use_undetected_playwright,
                    cache=self.cache,
                    user_action_layer=self.user_action_layer,
                    scroll_into_view=self.scroll_into_view,
                    mask_fingerprint=self.mask_fingerprint,
                )
                page = Page(_page, self, self.faker)
                frame = Frame(_frame, page)

                source["context"], source["page"], source["frame"] = (
                    context,
                    page,
                    frame,
                )

                if isinstance(element, PlaywrightElementHandle):
                    element = ElementHandle(element, page)
                else:
                    element = JSHandle(element, page)

                return callback(source, element)

            await self._origin_expose_binding(
                name, callback_proxy_handle, handle=handle
            )

        else:

            def callback_proxy(source: SourceDict, *args, **kwargs):
                _context: PlaywrightBrowserContext = source["context"]
                _page: PlaywrightPage = source["page"]
                _frame: PlaywrightFrame = source["frame"]

                context = BrowserContext(
                    _context,
                    self.proxy,
                    self.faker,
                    use_undetected_playwright=self.use_undetected_playwright,
                    cache=self.cache,
                    user_action_layer=self.user_action_layer,
                    scroll_into_view=self.scroll_into_view,
                    mask_fingerprint=self.mask_fingerprint,
                )
                page = Page(_page, self, self.faker)
                frame = Frame(_frame, page)

                source["context"], source["page"], source["frame"] = (
                    context,
                    page,
                    frame,
                )

                return callback(source, *args, **kwargs)

            await self._origin_expose_binding(
                name=name, callback=callback_proxy, handle=handle
            )
