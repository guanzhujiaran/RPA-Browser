"""
测试截图类 Action - Screenshot
"""
import pytest
import base64
from unittest.mock import AsyncMock, MagicMock


class TestScreenshotAction:
    """截图操作测试"""
    
    @pytest.mark.asyncio
    async def test_screenshot_element(self, mock_action_context):
        """测试截取元素"""
        from app.services.execution.actions.screenshot import ScreenshotAction
        
        action = ScreenshotAction()
        mock_action_context.params = {"selector": "#target"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["format"] == "png"
        assert "base64" in result.data
        assert len(result.data["base64"]) > 0
        locator = mock_action_context.page.locator.return_value
        locator.screenshot.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_screenshot_full_page(self, mock_action_context):
        """测试截取全页面"""
        from app.services.execution.actions.screenshot import ScreenshotAction
        
        action = ScreenshotAction()
        mock_action_context.params = {"full_page": True}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["format"] == "png"
        mock_action_context.page.screenshot.assert_called_once_with(type="png", full_page=True)
    
    @pytest.mark.asyncio
    async def test_screenshot_jpeg_format(self, mock_action_context):
        """测试 JPEG 格式"""
        from app.services.execution.actions.screenshot import ScreenshotAction
        
        action = ScreenshotAction()
        mock_action_context.params = {"type": "jpeg", "quality": 90}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["format"] == "jpeg"
        mock_action_context.page.screenshot.assert_called_once_with(type="jpeg", quality=90)
    
    @pytest.mark.asyncio
    async def test_screenshot_png_transparent(self, mock_action_context):
        """测试 PNG 透明背景"""
        from app.services.execution.actions.screenshot import ScreenshotAction
        
        action = ScreenshotAction()
        mock_action_context.params = {"type": "png", "omit_background": True}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        mock_action_context.page.screenshot.assert_called_once_with(type="png", omit_background=True)
    
    @pytest.mark.asyncio
    async def test_screenshot_base64_encoding(self, mock_action_context):
        """测试 Base64 编码"""
        from app.services.execution.actions.screenshot import ScreenshotAction
        
        test_image_data = b"test_png_data"
        mock_action_context.page.screenshot = AsyncMock(return_value=test_image_data)
        
        action = ScreenshotAction()
        mock_action_context.params = {}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        # 验证 Base64 编码正确
        decoded = base64.b64decode(result.data["base64"])
        assert decoded == test_image_data