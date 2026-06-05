"""
测试导航操作
"""
import pytest
from playwright.async_api import Page

from app.models.execution.action_params import NavigateParams, NewPageParams


class TestNavigateAction:
    """导航操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_url(self):
        """测试导航到指定 URL"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=NavigateParams(url="about:blank"),
        )

        result = await action.execute()
        assert result.success
        assert "about:blank" in self.page.url

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_params(self):
        """测试带参数的导航"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=NavigateParams(url="about:blank", timeout=60000),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_wait_until_domcontentloaded(self):
        """测试不同 wait_until 参数"""
        from app.services.execution.actions.navigation import NavigateAction
        from app.models.execution.enums import WaitUntilEnum

        action = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=NavigateParams(url="about:blank", wait_until=WaitUntilEnum.DOMCONTENTLOADED),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_wait_until_load(self):
        """测试 wait_until=load"""
        from app.services.execution.actions.navigation import NavigateAction
        from app.models.execution.enums import WaitUntilEnum

        action = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=NavigateParams(url="about:blank", wait_until=WaitUntilEnum.LOAD),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_wait_until_networkidle(self):
        """测试 wait_until=networkidle"""
        from app.services.execution.actions.navigation import NavigateAction
        from app.models.execution.enums import WaitUntilEnum

        action = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=NavigateParams(url="about:blank", wait_until=WaitUntilEnum.NETWORKIDLE),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_missing_url(self):
        """测试缺少 URL 参数"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=NavigateParams(url=""),
        )

        result = await action.execute()
        assert not result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_new_page_with_url(self):
        """测试新建页面带 URL"""
        from app.services.execution.actions.navigation import NewPageAction

        action = NewPageAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=NewPageParams(url="about:blank"),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_new_page_without_url(self):
        """测试新建页面不带 URL"""
        from app.services.execution.actions.navigation import NewPageAction

        action = NewPageAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=NewPageParams(),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_with_variables(self):
        """测试使用变量"""
        from app.services.execution.actions.navigation import NavigateAction

        action = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=NavigateParams(url="about:blank"),
        )

        result = await action.execute()
        assert result.success
        assert "about:blank" in self.page.url