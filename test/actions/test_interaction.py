"""
测试交互类 Action - Click, Input, Scroll, Wait, Hover
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestClickAction:
    """点击操作测试"""
    
    @pytest.mark.asyncio
    async def test_click_with_selector(self, mock_action_context):
        """测试使用 selector 点击"""
        from app.services.execution.actions.interaction import ClickAction
        
        action = ClickAction()
        mock_action_context.params = {"selector": "#btn"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["selector"] == "#btn"
        mock_action_context.page.locator.assert_called_once_with("#btn")
    
    @pytest.mark.asyncio
    async def test_click_double_click(self, mock_action_context):
        """测试双击操作"""
        from app.services.execution.actions.interaction import ClickAction
        
        action = ClickAction()
        mock_action_context.params = {"selector": "#btn", "click_count": 2}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        locator = mock_action_context.page.locator.return_value
        locator.dblclick.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_click_with_position(self, mock_action_context):
        """测试使用位置点击（无 selector）"""
        from app.services.execution.actions.interaction import ClickAction
        
        action = ClickAction()
        mock_action_context.params = {"position": {"x": 100, "y": 200}}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        mock_action_context.page.click.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_click_without_selector_and_position(self, mock_action_context):
        """测试无 selector 和 position 的情况"""
        from app.services.execution.actions.interaction import ClickAction
        
        action = ClickAction()
        mock_action_context.params = {}
        
        result = await action.execute(mock_action_context)
        
        assert not result.success
        assert "没有 selector 时必须提供 position" in result.error


class TestInputAction:
    """输入操作测试"""
    
    @pytest.mark.asyncio
    async def test_input_with_selector(self, mock_action_context):
        """测试输入操作"""
        from app.services.execution.actions.interaction import InputAction
        
        action = InputAction()
        mock_action_context.params = {"selector": "#input", "value": "test value"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["selector"] == "#input"
        assert result.data["value_length"] == 10
        locator = mock_action_context.page.locator.return_value
        locator.fill.assert_called_once_with("test value")
    
    @pytest.mark.asyncio
    async def test_input_missing_selector(self, mock_action_context):
        """测试缺少 selector 时使用 page.locator(None)"""
        from app.services.execution.actions.interaction import InputAction
        
        action = InputAction()
        mock_action_context.params = {"value": "test"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["selector"] is None


class TestScrollAction:
    """滚动操作测试"""
    
    @pytest.mark.asyncio
    async def test_scroll_with_selector(self, mock_action_context):
        """测试滚动到元素"""
        from app.services.execution.actions.interaction import ScrollAction
        
        action = ScrollAction()
        mock_action_context.params = {"selector": "#target"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        locator = mock_action_context.page.locator.return_value
        locator.scroll_into_view_if_needed.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_scroll_without_selector(self, mock_action_context):
        """测试无 selector 时滚动到顶部"""
        from app.services.execution.actions.interaction import ScrollAction
        
        action = ScrollAction()
        mock_action_context.params = {}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        mock_action_context.page.evaluate.assert_called_once_with("window.scrollTo(0, 0)")


class TestWaitAction:
    """等待操作测试"""
    
    @pytest.mark.asyncio
    async def test_wait_with_selector(self, mock_action_context):
        """测试等待元素出现"""
        from app.services.execution.actions.interaction import WaitAction
        
        action = WaitAction()
        mock_action_context.params = {"selector": "#target", "state": "visible"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        locator = mock_action_context.page.locator.return_value
        locator.wait_for.assert_called_once_with(state="visible")
    
    @pytest.mark.asyncio
    async def test_wait_fixed_time(self, mock_action_context):
        """测试固定时间等待"""
        from app.services.execution.actions.interaction import WaitAction
        
        action = WaitAction()
        mock_action_context.params = {"timeout": 1000}
        
        result = await action.execute(mock_action_context)
        
        assert result.success


class TestHoverAction:
    """悬停操作测试"""
    
    @pytest.mark.asyncio
    async def test_hover_with_selector(self, mock_action_context):
        """测试使用 selector 悬停"""
        from app.services.execution.actions.interaction import HoverAction
        
        action = HoverAction()
        mock_action_context.params = {"selector": "#target"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["selector"] == "#target"
        locator = mock_action_context.page.locator.return_value
        locator.hover.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_hover_with_position(self, mock_action_context):
        """测试使用位置悬停（无 selector）"""
        from app.services.execution.actions.interaction import HoverAction
        
        action = HoverAction()
        mock_action_context.params = {"position": {"x": 50, "y": 50}}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        mock_action_context.page.hover.assert_called_once_with(x=50, y=50)
    
    @pytest.mark.asyncio
    async def test_hover_with_modifiers(self, mock_action_context):
        """测试带修饰键的悬停"""
        from app.services.execution.actions.interaction import HoverAction
        
        action = HoverAction()
        mock_action_context.params = {"selector": "#target", "modifiers": ["Control"]}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        locator = mock_action_context.page.locator.return_value
        locator.hover.assert_called_once_with(modifiers=["Control"])
    
    @pytest.mark.asyncio
    async def test_hover_without_selector_and_position(self, mock_action_context):
        """测试无 selector 和 position 的情况"""
        from app.services.execution.actions.interaction import HoverAction
        
        action = HoverAction()
        mock_action_context.params = {}
        
        result = await action.execute(mock_action_context)
        
        assert not result.success
        assert "没有 selector 时必须提供 position" in result.error