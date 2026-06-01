"""
测试导航类 Action - Navigate, NewPage
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestNavigateAction:
    """导航操作测试"""
    
    @pytest.mark.asyncio
    async def test_navigate_with_url(self, mock_action_context):
        """测试导航到 URL"""
        from app.services.execution.actions.navigation import NavigateAction
        
        action = NavigateAction()
        mock_action_context.params = {"url": "https://example.com"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["url"] == "https://example.com"
        assert result.data["status"] == 200
        mock_action_context.page.goto.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_navigate_with_https_url(self, mock_action_context):
        """测试自动添加 https 协议"""
        from app.services.execution.actions.navigation import NavigateAction
        
        action = NavigateAction()
        mock_action_context.params = {"url": "example.com"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        # 验证 URL 被转换为 https
        call_args = mock_action_context.page.goto.call_args[0][0]
        assert call_args.startswith("https://")
    
    @pytest.mark.asyncio
    async def test_navigate_with_www_url(self, mock_action_context):
        """测试 www 开头的 URL"""
        from app.services.execution.actions.navigation import NavigateAction
        
        action = NavigateAction()
        mock_action_context.params = {"url": "www.example.com"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        call_args = mock_action_context.page.goto.call_args[0][0]
        assert call_args == "https://www.example.com"
    
    @pytest.mark.asyncio
    async def test_navigate_invalid_url(self, mock_action_context):
        """测试无效 URL"""
        from app.services.execution.actions.navigation import NavigateAction
        
        action = NavigateAction()
        mock_action_context.params = {"url": "invalid-url"}
        
        result = await action.execute(mock_action_context)
        
        assert not result.success
        assert "无效的 URL 格式" in result.error
    
    @pytest.mark.asyncio
    async def test_navigate_localhost_blocked(self, mock_action_context):
        """测试 localhost 被阻止"""
        from app.services.execution.actions.navigation import NavigateAction
        
        action = NavigateAction()
        mock_action_context.params = {"url": "http://localhost:8080"}
        
        result = await action.execute(mock_action_context)
        
        assert not result.success
        assert "禁止访问 localhost" in result.error
    
    @pytest.mark.asyncio
    async def test_navigate_private_ip_blocked(self, mock_action_context):
        """测试私有 IP 被阻止"""
        from app.services.execution.actions.navigation import NavigateAction
        
        action = NavigateAction()
        mock_action_context.params = {"url": "http://192.168.1.1"}
        
        result = await action.execute(mock_action_context)
        
        assert not result.success
        assert "禁止访问私有地址" in result.error


class TestNewPageAction:
    """新建页面操作测试"""
    
    @pytest.mark.asyncio
    async def test_new_page_without_url(self, mock_action_context, mock_page):
        """测试创建空白页面"""
        from app.services.execution.actions.navigation import NewPageAction
        
        new_page_mock = MagicMock()
        new_page_mock.url = "about:blank"
        new_page_mock.bring_to_front = AsyncMock(return_value=None)
        mock_action_context.page.context.new_page = AsyncMock(return_value=new_page_mock)
        
        action = NewPageAction()
        mock_action_context.params = {}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["page_created"]
        assert result.data["url"] == "about:blank"
    
    @pytest.mark.asyncio
    async def test_new_page_with_url(self, mock_action_context, mock_page):
        """测试创建页面并导航"""
        from app.services.execution.actions.navigation import NewPageAction
        
        new_page_mock = MagicMock()
        new_page_mock.url = "https://example.com"
        new_page_mock.bring_to_front = AsyncMock(return_value=None)
        new_page_mock.goto = AsyncMock(return_value=MagicMock(status=200))
        mock_action_context.page.context.new_page = AsyncMock(return_value=new_page_mock)
        
        action = NewPageAction()
        mock_action_context.params = {"url": "https://example.com"}
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["page_created"]
        assert result.data["url"] == "https://example.com"
    
    @pytest.mark.asyncio
    async def test_new_page_without_page_and_browser(self, mock_action_context):
        """测试没有 page 和 browser 的情况"""
        from app.services.execution.actions.navigation import NewPageAction
        
        action = NewPageAction()
        mock_action_context.params = {}
        mock_action_context.page = None
        mock_action_context.browser = None
        
        result = await action.execute(mock_action_context)
        
        assert not result.success
        assert "浏览器对象不可用" in result.error
    
    @pytest.mark.asyncio
    async def test_new_page_security_block(self, mock_action_context, mock_page):
        """测试安全检查阻止"""
        from app.services.execution.actions.navigation import NewPageAction
        
        new_page_mock = MagicMock()
        new_page_mock.url = "http://localhost:8080"
        new_page_mock.close = AsyncMock(return_value=None)
        mock_action_context.page.context.new_page = AsyncMock(return_value=new_page_mock)
        
        action = NewPageAction()
        mock_action_context.params = {"url": "http://localhost:8080"}
        
        result = await action.execute(mock_action_context)
        
        assert not result.success
        assert "禁止访问 localhost" in result.error