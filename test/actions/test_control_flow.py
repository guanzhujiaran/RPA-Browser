"""
测试控制流操作 - 使用 aiounittest.AsyncTestCase 模式
直接执行真实逻辑，不使用 mock
"""
import pytest
import aiounittest
from playwright.async_api import Page


class TestLoopAction(aiounittest.AsyncTestCase):
    """循环操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_loop_with_count_and_click(self):
        """测试按次数循环 - 真实执行循环内的点击操作"""
        from app.services.execution.actions.control_flow import LoopAction

        await self.page.set_content(
            "<html><body>"
            "<button class='item' data-index='0'>Item 0</button>"
            "<button class='item' data-index='1'>Item 1</button>"
            "<button class='item' data-index='2'>Item 2</button>"
            "</body></html>"
        )

        action = LoopAction(
            page=self.page,
            params={
                "count": 3,
                "_children_steps": [
                    {"action_id": "click", "params": {"selector": ".item:nth-child({{state.loop.index}})"}}
                ],
            },
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_loop_with_items(self):
        """测试按列表循环"""
        from app.services.execution.actions.control_flow import LoopAction

        await self.page.set_content(
            "<html><body>"
            "<input id='input_a' value=''>"
            "<input id='input_b' value=''>"
            "</body></html>"
        )

        action = LoopAction(
            page=self.page,
            params={
                "items": ["a", "b"],
                "_children_steps": [
                    {"action_id": "input", "params": {"selector": "#input_{{state.loop.current_item}}", "value": "value_{{state.loop.current_item}}"}}
                ],
            },
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_loop_missing_params(self):
        """测试缺少循环参数"""
        from app.services.execution.actions.control_flow import LoopAction

        action = LoopAction(
            page=self.page,
            params={},
        )

        result = await action.execute()
        assert not result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_loop_with_screenshot(self):
        """测试循环内包含截图操作"""
        from app.services.execution.actions.control_flow import LoopAction

        await self.page.set_content(
            "<html><body>"
            "<div id='slide1' style='width: 100px; height: 50px; background: red;'>Slide 1</div>"
            "<div id='slide2' style='width: 100px; height: 50px; background: blue;'>Slide 2</div>"
            "</body></html>"
        )

        action = LoopAction(
            page=self.page,
            params={
                "count": 2,
                "_children_steps": [
                    {"action_id": "screenshot", "params": {}}
                ],
            },
        )

        result = await action.execute()
        assert result.success


class TestIfElseAction(aiounittest.AsyncTestCase):
    """条件分支操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_if_else_with_true_condition(self):
        """测试条件为真的分支 - 执行 true 分支内的操作"""
        from app.services.execution.actions.control_flow import IfElseAction

        await self.page.set_content(
            "<html><body>"
            "<button id='true_btn'>True</button>"
            "</body></html>"
        )

        action = IfElseAction(
            page=self.page,
            params={
                "condition": "1 == 1",
                "_true_branch_steps": [
                    {"action_id": "click", "params": {"selector": "#true_btn"}}
                ],
                "_false_branch_steps": [],
            },
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_if_else_with_false_condition(self):
        """测试条件为假的分支 - 执行 false 分支内的操作"""
        from app.services.execution.actions.control_flow import IfElseAction

        await self.page.set_content(
            "<html><body>"
            "<button id='false_btn'>False</button>"
            "</body></html>"
        )

        action = IfElseAction(
            page=self.page,
            params={
                "condition": "1 == 2",
                "_true_branch_steps": [],
                "_false_branch_steps": [
                    {"action_id": "click", "params": {"selector": "#false_btn"}}
                ],
            },
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_if_else_missing_condition(self):
        """测试缺少条件参数"""
        from app.services.execution.actions.control_flow import IfElseAction

        action = IfElseAction(
            page=self.page,
            params={},
        )

        result = await action.execute()
        assert not result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_if_else_with_screenshot_branch(self):
        """测试条件分支包含截图"""
        from app.services.execution.actions.control_flow import IfElseAction

        await self.page.set_content(
            "<html><body>"
            "<div id='content'>Content</div>"
            "</body></html>"
        )

        action = IfElseAction(
            page=self.page,
            params={
                "condition": "'content' in page.content()",
                "_true_branch_steps": [
                    {"action_id": "screenshot", "params": {}}
                ],
                "_false_branch_steps": [],
            },
        )

        result = await action.execute()
        assert result.success
