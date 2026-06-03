"""
测试导航操作 - 使用 aiounittest.AsyncTestCase 模式
浏览器由 pytest-playwright-asyncio 提供 page fixture
参考: https://playwright.dev/python/docs/test-runners#using-with-unittesttestcase
"""
import pytest
import aiounittest
from playwright.async_api import Page


class TestNavigateAction(aiounittest.AsyncTestCase):
    """导航操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_url(self):
        """测试导航到指定 URL"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction(
            page=self.page,
            params={"url": "https://example.com"},
        )

        result = await action.execute()
        assert result.success
        assert "example.com" in self.page.url

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_params(self):
        """测试带参数的导航"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction(
            page=self.page,
            params={"url": "https://example.com", "timeout": 60000},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_wait_until_domcontentloaded(self):
        """测试不同 wait_until 参数"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction(
            page=self.page,
            params={"url": "https://example.com", "wait_until": "domcontentloaded"},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_wait_until_load(self):
        """测试 wait_until=load"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction(
            page=self.page,
            params={"url": "https://example.com", "wait_until": "load"},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_wait_until_networkidle(self):
        """测试 wait_until=networkidle"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction(
            page=self.page,
            params={"url": "https://example.com", "wait_until": "networkidle"},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_commit(self):
        """测试 commit 参数"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction(
            page=self.page,
            params={"url": "https://example.com", "commit": True},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_replace_state(self):
        """测试 replace_state 参数"""
        from app.services.execution.actions.navigation import NavigateAction

        await self.page.goto("https://example.com")

        action = NavigateAction(
            page=self.page,
            params={"url": "https://example.com/", "replace_state": True},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_missing_url(self):
        """测试缺少 URL 参数"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction(
            page=self.page,
            params={},
        )

        result = await action.execute()
        assert not result.success
        assert "url" in result.error.lower()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_new_page_with_url(self):
        """测试新建页面带 URL"""
        from app.services.execution.actions.navigation import NewPageAction

        action = NewPageAction(
            page=self.page,
            browser=self.page.context.browser,
            params={"url": "https://example.com"},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_new_page_without_url(self):
        """测试新建页面不带 URL"""
        from app.services.execution.actions.navigation import NewPageAction

        action = NewPageAction(
            page=self.page,
            browser=self.page.context.browser,
            params={},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_go_back(self):
        """测试返回上一页"""
        from app.services.execution.actions.navigation import GoBackAction

        await self.page.goto("https://example.com")
        await self.page.goto("https://example.com/")

        action = GoBackAction(
            page=self.page,
            params={"timeout": 10000},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_go_forward(self):
        """测试前进"""
        from app.services.execution.actions.navigation import GoForwardAction

        await self.page.goto("https://example.com")
        await self.page.goto("https://example.com/")
        await self.page.go_back()

        action = GoForwardAction(
            page=self.page,
            params={"timeout": 10000},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reload(self):
        """测试刷新页面"""
        from app.services.execution.actions.navigation import ReloadAction

        await self.page.goto("https://example.com")

        action = ReloadAction(
            page=self.page,
            params={"timeout": 10000},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_go_back_without_history(self):
        """测试无历史记录时返回"""
        from app.services.execution.actions.navigation import GoBackAction

        await self.page.goto("https://example.com")

        action = GoBackAction(
            page=self.page,
            params={"timeout": 10000},
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_variables(self):
        """测试使用变量"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction(
            page=self.page,
            params={"url": "https://{domain}.com"},
            variables={"domain": "example"},
        )

        result = await action.execute()
        assert result.success
        assert "example.com" in self.page.url
