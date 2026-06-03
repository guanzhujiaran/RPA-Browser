"""
测试截图操作 - 使用 aiounittest.AsyncTestCase 模式
"""
import pytest
import aiounittest
from playwright.async_api import Page


class TestScreenshotAction(aiounittest.AsyncTestCase):
    """截图操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_screenshot_full_page(self):
        """测试全屏截图"""
        from app.services.execution.actions.screenshot import ScreenshotAction

        await self.page.set_content("<html><body><h1>Test Page</h1></body></html>")

        action = ScreenshotAction(
            page=self.page,
            params={},
        )

        result = await action.execute()
        assert result.success
        assert result.data["format"] == "png"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_screenshot_with_selector(self):
        """测试指定元素截图"""
        from app.services.execution.actions.screenshot import ScreenshotAction

        await self.page.set_content("<html><body><div id='target' style='height: 50px;'>Target</div></body></html>")

        action = ScreenshotAction(
            page=self.page,
            params={"selector": "#target"},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_screenshot_jpeg(self):
        """测试 JPEG 格式截图"""
        from app.services.execution.actions.screenshot import ScreenshotAction

        await self.page.set_content("<html><body>Test</body></html>")

        action = ScreenshotAction(
            page=self.page,
            params={"type": "jpeg", "quality": 80},
        )

        result = await action.execute()
        assert result.success
        assert result.data["format"] == "jpeg"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_screenshot_full_page_false(self):
        """测试非全屏截图"""
        from app.services.execution.actions.screenshot import ScreenshotAction

        await self.page.set_content("<html><body>Test</body></html>")

        action = ScreenshotAction(
            page=self.page,
            params={"full_page": False},
        )

        result = await action.execute()
        assert result.success
        assert result.data["format"] == "png"
