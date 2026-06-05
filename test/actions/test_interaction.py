"""
测试交互操作
"""
import pytest
from playwright.async_api import Page

from app.models.execution.action_params import (
    ClickParams, InputParams, ScrollParams, HoverParams,
)
from app.models.execution.enums import MouseButtonEnum


class TestClickAction:
    """点击操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_click_element(self):
        """测试点击元素"""
        from app.services.execution.actions.interaction import ClickAction

        await self.page.set_content("<html><body><button id='btn'>Click</button></body></html>")

        action = ClickAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ClickParams(selector="#btn"),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_double_click(self):
        """测试双击"""
        from app.services.execution.actions.interaction import ClickAction

        await self.page.set_content("<html><body><button id='btn'>Double Click</button></body></html>")

        action = ClickAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ClickParams(selector="#btn", click_count=2),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_right_click(self):
        """测试右键点击"""
        from app.services.execution.actions.interaction import ClickAction

        await self.page.set_content("<html><body><button id='btn'>Right Click</button></body></html>")

        action = ClickAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ClickParams(selector="#btn", button=MouseButtonEnum.RIGHT),
        )

        result = await action.execute()
        assert result.success


class TestInputAction:
    """输入操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_text(self):
        """测试输入文本"""
        from app.services.execution.actions.interaction import InputAction

        await self.page.set_content("<html><body><input id='input' type='text'></body></html>")

        action = InputAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=InputParams(selector="#input", value="Hello World"),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_without_selector(self):
        """测试输入操作"""
        from app.services.execution.actions.interaction import InputAction

        await self.page.set_content("<html><body><input id='input' type='text'></body></html>")
        await self.page.click("#input")

        action = InputAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=InputParams(selector="#input", value="Keyboard Input"),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_with_clear(self):
        """测试输入"""
        from app.services.execution.actions.interaction import InputAction

        await self.page.set_content("<html><body><input id='input' type='text' value='old'></body></html>")

        action = InputAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=InputParams(selector="#input", value="new text"),
        )

        result = await action.execute()
        assert result.success


class TestScrollAction:
    """滚动操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_scroll_to_bottom(self):
        """测试滚动到页面底部"""
        from app.services.execution.actions.interaction import ScrollAction

        await self.page.set_content("<html><body><div style='height: 3000px;'>Long Content</div></body></html>")

        action = ScrollAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ScrollParams(),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_scroll_to_element(self):
        """测试滚动到指定元素"""
        from app.services.execution.actions.interaction import ScrollAction

        await self.page.set_content("<html><body><div id='target' style='margin-top: 500px;'>Target</div></body></html>")

        action = ScrollAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ScrollParams(selector="#target"),
        )

        result = await action.execute()
        assert result.success


class TestHoverAction:
    """悬停操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_hover_element(self):
        """测试悬停元素"""
        from app.services.execution.actions.interaction import HoverAction

        await self.page.set_content("<html><body><div id='target' style='width: 50px; height: 50px; background: green;'>Hover</div></body></html>")

        action = HoverAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=HoverParams(selector="#target", timeout=5000),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_hover_by_position(self):
        """测试按坐标悬停"""
        from app.services.execution.actions.interaction import HoverAction
        from app.models.execution.action_params import Position

        await self.page.set_content("<html><body><div style='width: 100px; height: 100px; background: blue;'>Area</div></body></html>")

        action = HoverAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=HoverParams(position=Position(x=10, y=10), timeout=5000),
        )

        result = await action.execute()
        assert result.success