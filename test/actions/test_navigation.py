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
            params=NavigateParams(
                url="about:blank", wait_until=WaitUntilEnum.DOMCONTENTLOADED
            ),
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
            params=NavigateParams(
                url="about:blank", wait_until=WaitUntilEnum.NETWORKIDLE
            ),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_navigate_missing_url(self):
        """测试缺少 URL 参数（mode=goto）

        旧行为：空 url 在动作执行阶段失败。新增 mode 后，校验前移到参数模型
        （``mode=goto`` 必须提供 url），因此构造参数即失败、执行阶段同样不会成功。
        """
        from pydantic import ValidationError

        from app.services.execution.actions.navigation import NavigateAction

        with pytest.raises(ValidationError):
            NavigateParams(url="")

        # 绕过模型构造（模拟旧 JSON 直接传 dict）时，仍应在执行阶段失败
        action = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params={"url": "", "mode": "goto"},
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


class TestNavigateMode:
    """navigate 的 mode 扩展（reload / back / forward）"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reload_does_not_need_url(self):
        """mode=reload 不要求 url，且会重载当前文档"""
        from app.models.execution.enums import NavigateModeEnum
        from app.services.execution.actions.navigation import NavigateAction

        await self.page.goto("about:blank")
        await self.page.set_content("<div id='marker'>x</div>")
        assert await self.page.locator("#marker").count() == 1

        action = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params=NavigateParams(mode=NavigateModeEnum.RELOAD),
        )
        result = await action.execute()

        assert result.success
        # 文档被重载：注入的节点消失（about:blank 无 HTTP 响应，status 为 0）
        assert await self.page.locator("#marker").count() == 0
        assert result.data.status == 0

    @pytest.mark.asyncio(loop_scope="session")
    async def test_back_and_forward(self):
        """mode=back / forward 复用当前页面历史"""
        from app.models.execution.enums import NavigateModeEnum
        from app.services.execution.actions.navigation import NavigateAction

        await self.page.goto("about:blank#p1")
        await self.page.goto("about:blank#p2")

        back = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params=NavigateParams(mode=NavigateModeEnum.BACK),
        )
        assert (await back.execute()).success
        assert self.page.url.endswith("#p1")

        forward = NavigateAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params=NavigateParams(mode=NavigateModeEnum.FORWARD),
        )
        assert (await forward.execute()).success
        assert self.page.url.endswith("#p2")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_goto_still_needs_url(self):
        """mode=goto（默认）仍然要求 url，缺失即校验失败"""
        from pydantic import ValidationError

        from app.models.execution.enums import NavigateModeEnum

        with pytest.raises(ValidationError):
            NavigateParams(mode=NavigateModeEnum.GOTO)

        # 其他模式则允许不带 url
        assert NavigateParams(mode=NavigateModeEnum.RELOAD).url is None
