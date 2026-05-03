"""
RPA 操作服务 - 处理浏览器自动化操作

此模块提供各种 RPA 操作，如点击、填充、滚动、截图等。
"""

import base64
from sqlmodel import SQLModel, Field
from playwright.async_api import Page, BrowserContext
from app.models.runtime.operations import (
    RPAClickParams,
    RPAFillParams,
    RPAScrollParams,
    RPAScreenshotParams,
    RPAEvaluateParams,
    RPAWaitParams,
    RPANavigateParams,
    RPAResponse,
)
from app.models.runtime.control import BrowserInfoData
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    PluginedSessionInfo,
)


class PageInfo(SQLModel):
    """页面信息模型"""
    index: int = Field(description="页面索引（从0开始，可能变化）")
    page_id: str = Field(description="页面唯一ID（UUID，不会变化）")
    url: str = Field(description="页面URL")
    title: str = Field(description="页面标题")
    is_closed: bool = Field(description="是否已关闭")


class PagesListResponse(SQLModel):
    """页面列表响应模型"""
    pages_count: int = Field(description="页面总数")
    pages: list[PageInfo] = Field(description="页面列表")


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
    async def get_pages_list(session: PluginedSessionInfo) -> PagesListResponse:
        """
        获取浏览器所有页面列表（包含 page_id）
        
        Args:
            session: 浏览器会话
            
        Returns:
            PagesListResponse: 包含页面列表和当前激活页面索引
        """
        browser_context: BrowserContext = session.browser_context
        pages = browser_context.pages if browser_context else []

        page_info_list = []
        for i, page in enumerate(pages):
            try:
                # 确保页面有 page_id
                session._ensure_page_id(page)
                
                page_info = PageInfo(
                    index=i,  # 保留 index 用于向后兼容
                    page_id=page._webrtc_page_id,  # 新增 page_id
                    url=page.url,
                    title=await page.title() if not page.is_closed() else "",
                    is_closed=page.is_closed(),
                )
                page_info_list.append(page_info)
            except Exception as e:
                loguru.logger.warning(f"获取页面 {i} 信息失败: {e}")
                continue
        
        return PagesListResponse(
            pages_count=len(page_info_list),
            pages=page_info_list,
        )

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

    @staticmethod
    async def switch_page(session: PluginedSessionInfo, page_index: int) -> RPAResponse:
        """切换到指定页面"""
        page = await session.switch_to_page(page_index)
        return RPAResponse(
            success=True,
            data={
                "message": f"已切换到页面 {page_index}",
                "url": page.url,
                "title": await page.title(),
            },
        )


    @staticmethod
    async def close_page_by_index(
        session: PluginedSessionInfo, page_index: int
    ) -> RPAResponse:
        """关闭指定页面"""
        try:
            success = await session.close_page(page_index)
            if success:
                return RPAResponse(
                    success=True, data={"message": f"页面 {page_index} 已关闭"}
                )
            else:
                return RPAResponse(
                    success=False, error=f"关闭页面 {page_index} 失败"
                )
        except IndexError as e:
            return RPAResponse(success=False, error=str(e))
        except Exception as e:
            return RPAResponse(success=False, error=str(e))
