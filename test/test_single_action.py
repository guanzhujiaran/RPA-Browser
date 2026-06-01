"""
单个 Action 执行测试

测试内容：
1. ClickAction - 点击操作
2. InputAction - 输入操作
3. ScrollAction - 滚动操作
4. WaitAction - 等待操作
5. NavigateAction - 导航操作
6. ScreenshotAction - 截图操作
7. LLMAction - LLM 调用（mock）
"""

import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.execution.actions.interaction import (
    ClickAction, InputAction, ScrollAction, WaitAction
)
from app.services.execution.actions.navigation import NavigateAction, NewPageAction
from app.services.execution.actions.screenshot import ScreenshotAction
from app.services.execution.actions.control_flow import LoopAction, IfElseAction
from app.models.database.workflow.models import ActionContext, ActionResult


class TestClickAction:
    """点击操作测试"""
    
    @pytest.mark.asyncio
    async def test_click_single(self, mock_page, mock_browser):
        """测试单次点击"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": "#btn"},
            user_data={}
        )
        
        action = ClickAction()
        result = await action.execute(ctx)
        
        assert result.success is True
        assert mock_page._locator_map["#btn"]._clicked is True
    
    @pytest.mark.asyncio
    async def test_click_with_button(self, mock_page, mock_browser):
        """测试指定按钮点击"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": "#btn", "button": "right"},
            user_data={}
        )
        
        action = ClickAction()
        result = await action.execute(ctx)
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_click_double(self, mock_page, mock_browser):
        """测试双击"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": "#btn", "click_count": 2},
            user_data={}
        )
        
        action = ClickAction()
        result = await action.execute(ctx)
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_click_invalid_selector(self, mock_page, mock_browser):
        """测试无效选择器"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": ""},
            user_data={}
        )
        
        action = ClickAction()
        result = await action.execute(ctx)
        
        # 空选择器应该失败
        assert result.success is False or result.error is not None


class TestInputAction:
    """输入操作测试"""
    
    @pytest.mark.asyncio
    async def test_input_text(self, mock_page, mock_browser):
        """测试文本输入"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": "#input", "value": "hello world"},
            user_data={}
        )
        
        action = InputAction()
        result = await action.execute(ctx)
        
        assert result.success is True
        assert mock_page._locator_map["#input"]._filled_value == "hello world"
    
    @pytest.mark.asyncio
    async def test_input_empty_value(self, mock_page, mock_browser):
        """测试清空输入"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": "#input", "value": ""},
            user_data={}
        )
        
        action = InputAction()
        result = await action.execute(ctx)
        
        assert result.success is True


class TestScrollAction:
    """滚动操作测试"""
    
    @pytest.mark.asyncio
    async def test_scroll_to_element(self, mock_page, mock_browser):
        """测试滚动到元素"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": "#result"},
            user_data={}
        )
        
        action = ScrollAction()
        result = await action.execute(ctx)
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_scroll_page(self, mock_page, mock_browser):
        """测试页面滚动"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={}
        )
        
        action = ScrollAction()
        result = await action.execute(ctx)
        
        assert result.success is True


class TestWaitAction:
    """等待操作测试"""
    
    @pytest.mark.asyncio
    async def test_wait_for_element(self, mock_page, mock_browser):
        """测试等待元素出现"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": "#btn", "state": "visible"},
            user_data={}
        )
        
        action = WaitAction()
        result = await action.execute(ctx)
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_wait_timeout(self, mock_page, mock_browser):
        """测试等待超时"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": "#nonexistent", "timeout": 100},
            user_data={}
        )
        
        action = WaitAction()
        result = await action.execute(ctx)
        
        # 元素不存在应该失败
        assert result.success is False


class TestNavigateAction:
    """导航操作测试"""
    
    @pytest.mark.asyncio
    async def test_navigate_to_url(self, mock_page, mock_browser):
        """测试导航到 URL"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"url": "https://example.com"},
            user_data={}
        )
        
        action = NavigateAction()
        result = await action.execute(ctx)
        
        assert result.success is True
        assert mock_page._goto_called is True
        assert mock_page._goto_url == "https://example.com"
    
    @pytest.mark.asyncio
    async def test_navigate_with_options(self, mock_page, mock_browser):
        """测试带选项的导航"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={
                "url": "https://example.com",
                "wait_until": "networkidle",
                "timeout": 30000
            },
            user_data={}
        )
        
        action = NavigateAction()
        result = await action.execute(ctx)
        
        assert result.success is True


class TestScreenshotAction:
    """截图操作测试"""
    
    @pytest.mark.asyncio
    async def test_screenshot_full_page(self, mock_page, mock_browser):
        """测试全页截图"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"full_page": True},
            user_data={}
        )
        
        action = ScreenshotAction()
        result = await action.execute(ctx)
        
        assert result.success is True
        assert result.data is not None
        assert mock_page._screenshot_called is True
    
    @pytest.mark.asyncio
    async def test_screenshot_element(self, mock_page, mock_browser):
        """测试元素截图"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={"selector": "#result"},
            user_data={}
        )
        
        action = ScreenshotAction()
        result = await action.execute(ctx)
        
        assert result.success is True


class TestNewPageAction:
    """新页面操作测试"""
    
    @pytest.mark.asyncio
    async def test_new_page(self, mock_page, mock_browser):
        """测试打开新页面"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={}
        )
        
        action = NewPageAction()
        result = await action.execute(ctx)
        
        assert result.success is True
