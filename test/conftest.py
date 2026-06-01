"""
测试配置文件 - Mock 外部依赖
"""
import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch


os.environ.setdefault("mysql_browser_info_url", "sqlite:///test.db")
os.environ.setdefault("RUNNING_MODE", "dev")


class MockLocator:
    """Mock Playwright Locator"""
    
    def __init__(self):
        self.click = AsyncMock(return_value=None)
        self.dblclick = AsyncMock(return_value=None)
        self.fill = AsyncMock(return_value=None)
        self.scroll_into_view_if_needed = AsyncMock(return_value=None)
        self.wait_for = AsyncMock(return_value=None)
        self.hover = AsyncMock(return_value=None)
        self.screenshot = AsyncMock(return_value=b"test_image_data")


class MockPage:
    """Mock Playwright Page"""
    
    def __init__(self):
        self.locator = MagicMock(return_value=MockLocator())
        self.click = AsyncMock(return_value=None)
        self.dblclick = AsyncMock(return_value=None)
        self.goto = AsyncMock(return_value=MagicMock(status=200))
        self.hover = AsyncMock(return_value=None)
        self.screenshot = AsyncMock(return_value=b"test_image_data")
        self.evaluate = AsyncMock(return_value=None)
        self.bring_to_front = AsyncMock(return_value=None)
        self.close = AsyncMock(return_value=None)
        self.url = "https://example.com"
        self._is_closed = False
        
        self.context = MagicMock()
        self.context.pages = [self]
    
    def is_closed(self):
        """返回页面是否已关闭"""
        return self._is_closed


class MockBrowserContext:
    """Mock Playwright BrowserContext"""
    
    def __init__(self):
        self.new_page = AsyncMock(return_value=MockPage())
        self.pages = [MockPage()]


@pytest.fixture
def mock_page():
    """Mock Playwright Page 对象"""
    return MockPage()


@pytest.fixture
def mock_browser_context():
    """Mock Playwright BrowserContext"""
    return MockBrowserContext()


@pytest.fixture
def mock_action_context(mock_page):
    """Mock ActionContext"""
    from app.models.database.workflow.models import ActionContext
    return ActionContext(
        session_id="test_session",
        browser_id="test_browser",
        page=mock_page,
        browser=MockBrowserContext(),
        params={},
        user_data={},
    )


@pytest.fixture(autouse=True)
def mock_botright():
    """Mock botright 模块"""
    with patch.dict(sys.modules, {
        'botright': MagicMock(),
        'botright.botright': MagicMock(),
        'botright.playwright_mock': MagicMock(),
    }):
        yield


@pytest.fixture(autouse=True)
def mock_database():
    """Mock 数据库连接"""
    mock_session = AsyncMock()
    mock_manager = MagicMock()
    mock_manager.async_session.return_value.__aenter__.return_value = mock_session
    
    mock_session_module = MagicMock()
    mock_session_module.DatabaseSessionManager = mock_manager
    
    with patch.dict(sys.modules, {
        'app.utils.depends.session_manager': mock_session_module,
    }):
        yield mock_manager