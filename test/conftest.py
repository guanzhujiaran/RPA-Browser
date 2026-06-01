"""
测试公共 fixtures
"""
import sys
import os
from unittest.mock import AsyncMock, MagicMock

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock 掉不需要的模块（必须在 import 项目代码之前）
sys.modules["botright"] = MagicMock()
sys.modules["botright.botright"] = MagicMock()

# Mock fastapi 依赖
mock_fastapi = MagicMock()
mock_fastapi.Depends = lambda x: x
sys.modules["fastapi"] = mock_fastapi
sys.modules["fastapi.params"] = MagicMock()

# Mock session_manager 中的 DatabaseSessionManager
mock_session_manager = MagicMock()
mock_session_manager.DatabaseSessionManager = MagicMock()
sys.modules["app.utils.depends.session_manager"] = mock_session_manager

# Mock app.config.settings（需要设置环境变量）
os.environ["mysql_browser_info_url"] = "mysql://localhost:3306/test"
os.environ["RUNNING_MODE"] = "dev"
os.environ["RUNNING_MODE"] = "dev"

import pytest


# ============ Playwright Mock ============

class MockLocator:
    """模拟 Playwright Locator"""
    def __init__(self, selector: str = "", text_content: str = ""):
        self._selector = selector
        self._text_content = text_content
        self._clicked = False
        self._filled_value = None
        self._visible = True

    async def click(self, **kwargs):
        self._clicked = True

    async def dblclick(self, **kwargs):
        self._clicked = True

    async def fill(self, value: str, **kwargs):
        self._filled_value = value

    async def wait_for(self, state: str = "visible", **kwargs):
        pass

    async def scroll_into_view_if_needed(self, **kwargs):
        pass

    async def is_visible(self, **kwargs):
        return self._visible

    async def text_content(self, **kwargs):
        return self._text_content

    async def count(self, **kwargs):
        return 1

    async def evaluate(self, expression: str):
        return 0

    async def screenshot(self, **kwargs):
        return b"fake_screenshot_bytes"


class MockPage:
    """模拟 Playwright Page"""
    def __init__(self):
        self._url = "about:blank"
        self._goto_called = False
        self._goto_url = None
        self._screenshot_called = False
        self._screenshot_bytes = b"fake_screenshot"
        self._title = "Test Page"
        self._content = "<html><body>Test</body></html>"
        self._evaluated = []

        # locator mock
        self._locator_map = {
            "#btn": MockLocator("#btn"),
            "#input": MockLocator("#input"),
            "#search": MockLocator("#search"),
            "#result": MockLocator("#result", "result text"),
            "body": MockLocator("body"),
        }

    def locator(self, selector: str) -> MockLocator:
        if selector in self._locator_map:
            return self._locator_map[selector]
        return MockLocator(selector)

    async def goto(self, url: str, **kwargs):
        self._goto_called = True
        self._goto_url = url
        self._url = url

    async def title(self, **kwargs):
        return self._title

    async def content(self, **kwargs):
        return self._content

    async def screenshot(self, **kwargs):
        self._screenshot_called = True
        return self._screenshot_bytes

    async def evaluate(self, expression: str):
        self._evaluated.append(expression)
        return 0

    async def wait_for_timeout(self, timeout: int):
        pass

    async def wait_for_load_state(self, state: str = "load"):
        pass

    async def close(self):
        pass

    @property
    def url(self):
        return self._url

    @property
    def context(self):
        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=MockPage())
        return mock_context


class MockBrowserContext:
    """模拟 BrowserContext"""
    def __init__(self):
        self.pages = [MockPage()]

    async def new_page(self):
        page = MockPage()
        self.pages.append(page)
        return page


# ============ Fixtures ============

@pytest.fixture
def mock_page():
    return MockPage()


@pytest.fixture
def mock_browser():
    return MockBrowserContext()


@pytest.fixture
def mock_session():
    """模拟数据库 session"""
    session = MagicMock()
    session.exec = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = MagicMock()
    return session


@pytest.fixture
def action_context(mock_page, mock_browser):
    """构建 ActionContext（使用 base.py 中的新体系）"""
    from app.services.execution.actions.base import ActionContext
    return ActionContext(
        session_id="test_session",
        browser_id="test_browser",
        page=mock_page,
        browser=mock_browser,
        variables={},
    )


@pytest.fixture
def old_action_context(mock_page, mock_browser):
    """构建旧体系 ActionContext（用于测试现有子类）"""
    # 使用 dataclass 风格的简单上下文
    from app.services.execution.actions.base import ActionContext
    ctx = ActionContext(
        session_id="test_session",
        browser_id="test_browser",
        page=mock_page,
        browser=mock_browser,
        variables={},
    )
    # 添加旧体系兼容属性
    ctx.params = {}
    ctx.user_data = {}
    return ctx
