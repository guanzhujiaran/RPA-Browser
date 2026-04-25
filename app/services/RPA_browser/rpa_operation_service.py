"""
RPA 操作服务 - 处理浏览器自动化操作

此模块提供各种 RPA 操作，如点击、填充、滚动、截图等。
"""
import base64
from playwright.async_api import Page, BrowserContext
from loguru import logger

from app.models.RPA_browser.browser_info_model import (
    RPAClickParams,
    RPAFillParams,
    RPAScrollParams,
    RPAScreenshotParams,
    RPAEvaluateParams,
    RPAWaitParams,
    RPANavigateParams,
    RPAResponse,
)
from app.models.RPA_browser.live_control_models import BrowserInfoData
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    PluginedSessionInfo,
)


class RPAOperationService:
    """RPA操作服务类"""

    @staticmethod
    async def click_element(page: Page, params: RPAClickParams) -> RPAResponse:
        """点击元素"""
        try:
            element = page.locator(params.selector)
            await element.wait_for(state="visible", timeout=params.timeout)
            await element.click()
            return RPAResponse(success=True, data={"message": "点击成功"})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def fill_form(page: Page, params: RPAFillParams) -> RPAResponse:
        """填充表单"""
        try:
            element = page.locator(params.selector)
            await element.wait_for(state="visible", timeout=params.timeout)
            await element.fill(params.value)
            return RPAResponse(success=True, data={"message": "填充成功"})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def scroll_page(page: Page, params: RPAScrollParams) -> RPAResponse:
        """滚动页面"""
        try:
            await page.evaluate(
                f"window.scrollTo({{top: {params.y}, left: {params.x}, behavior: '{params.behavior}'}})"
            )
            return RPAResponse(success=True, data={"message": "滚动成功"})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def take_screenshot(page: Page, params: RPAScreenshotParams) -> RPAResponse:
        """截图"""
        try:
            if params.selector:
                element = page.locator(params.selector)
                await element.wait_for(state="visible", timeout=30000)
                screenshot_bytes = await element.screenshot(
                    type=params.type, quality=params.quality
                )
            else:
                screenshot_bytes = await page.screenshot(
                    full_page=params.full_page, type=params.type, quality=params.quality
                )

            image_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return RPAResponse(success=True, data={"image": image_base64})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def evaluate_script(page: Page, params: RPAEvaluateParams) -> RPAResponse:
        """执行JavaScript"""
        try:
            result = await page.evaluate(params.script, *params.args)
            return RPAResponse(success=True, data={"result": result})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def wait_for_element(page: Page, params: RPAWaitParams) -> RPAResponse:
        """等待元素"""
        try:
            if params.selector:
                element = page.locator(params.selector)
                await element.wait_for(state=params.state, timeout=params.timeout)
            else:
                await page.wait_for_timeout(params.timeout)
            return RPAResponse(success=True, data={"message": "等待完成"})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def navigate_to(page: Page, params: RPANavigateParams) -> RPAResponse:
        """导航到URL"""
        try:
            await page.goto(
                params.url, wait_until=params.wait_until, timeout=params.timeout
            )
            title = await page.title()
            current_url = page.url
            return RPAResponse(
                success=True, data={"title": title, "current_url": current_url}
            )
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def get_browser_info(session: PluginedSessionInfo) -> BrowserInfoData:
        """获取完整的浏览器信息"""
        browser_context: BrowserContext = session.browser_context
        pages = browser_context.pages if browser_context else []

        page_info_list = []
        for i, page in enumerate(pages):
            try:
                page_info = {
                    "index": i,
                    "url": page.url,
                    "title": await page.title() if not page.is_closed() else "",
                    "is_closed": page.is_closed(),
                }
                page_info_list.append(page_info)
            except Exception:
                continue

        return BrowserInfoData(
            browser_context={"pages_count": len(pages), "pages": page_info_list},
            plugins={
                "count": len(session.plugin_configs) if session.plugin_configs else 0,
                "enabled_plugins": [
                    {"name": config.name, "description": config.description}
                    for config in (
                        session.plugin_configs.values()
                        if session.plugin_configs
                        else []
                    )
                    if config.is_enabled
                ],
            },
            session={
                "mid": session.playwright_instance.mid,
                "browser_id": session.playwright_instance.browser_id,
                "headless": session.headless,
                "is_closed": session.is_closed,
            },
        )
