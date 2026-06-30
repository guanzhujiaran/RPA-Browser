"""
测试交互操作
"""
import pytest
from playwright.async_api import Page

from app.models.execution.action_params import (
    ClickParams, InputParams, ScrollParams, HoverParams,
    WaitParams, GetTextParams, GetWindowParams,
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

        await self.page.set_content("<html><body><button id='btn' onclick=\"window.__clicked=true\">Click</button></body></html>")

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

        await self.page.set_content("<html><body><button id='btn' onclick=\"var c=parseInt(this.innerText||'0');this.innerText=(c+1).toString()\">0</button></body></html>")

        action = ClickAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ClickParams(selector="#btn", click_count=2),
        )

        result = await action.execute()
        assert result.success
        # 验证双击确实触发了 2 次
        text = await self.page.inner_text("#btn")
        assert text == "2", f"Expected btn text '2' after double click, got '{text}'"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_right_click(self):
        """测试右键点击"""
        from app.services.execution.actions.interaction import ClickAction

        await self.page.set_content("<html><body><button id='btn' oncontextmenu=\"window.__rightClicked=true;return false;\">Right Click</button></body></html>")

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
        # 验证输入值已正确填入
        actual_value = await self.page.input_value("#input")
        assert actual_value == "Hello World", f"Expected 'Hello World', got '{actual_value}'"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_without_selector(self):
        """测试无 focus 时直接输入到已聚焦元素"""
        from app.services.execution.actions.interaction import InputAction

        await self.page.set_content("<html><body><input id='input' type='text'></body></html>")
        await self.page.click("#input")  # 先聚焦元素

        action = InputAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=InputParams(selector="#input", value="Keyboard Input"),
        )

        result = await action.execute()
        assert result.success
        # 验证键盘输入已生效
        actual_value = await self.page.input_value("#input")
        assert "Keyboard Input" in actual_value

    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_with_clear(self):
        """测试输入（替换已有内容）"""
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
        # 验证新值已替换旧值
        actual_value = await self.page.input_value("#input")
        assert actual_value == "new text", f"Expected 'new text', got '{actual_value}'"


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
        # 验证滚动位置确实改变了
        scroll_y = await self.page.evaluate("window.scrollY")
        assert scroll_y > 0, f"Expected scrollY > 0 after scroll, got {scroll_y}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_scroll_to_element(self):
        """测试滚动到指定元素"""
        from app.services.execution.actions.interaction import ScrollAction

        await self.page.set_content("<html><body><div id='target' style='margin-top: 2000px;'>Target</div></body></html>")

        action = ScrollAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ScrollParams(selector="#target"),
        )

        result = await action.execute()
        assert result.success
        # 验证目标元素在可视区域内
        is_visible = await self.page.evaluate("""
            () => {
                const el = document.querySelector('#target');
                const rect = el.getBoundingClientRect();
                return rect.top >= 0 && rect.bottom <= window.innerHeight;
            }
        """)
        assert is_visible, "Target element should be visible after scroll"


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


class TestWaitAction:
    """等待操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_wait_for_element_visible(self):
        """测试等待已存在的元素（立即返回 element_found=True）"""
        from app.services.execution.actions.interaction import WaitAction

        await self.page.set_content("<html><body><div id='target'>Target</div></body></html>")

        action = WaitAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=WaitParams(selector="#target", timeout=5000),
        )

        result = await action.execute()
        assert result.success
        assert result.data.element_found is True

    @pytest.mark.asyncio(loop_scope="session")
    async def test_wait_element_timeout(self):
        """测试等待不存在的元素超时（返回 success 但 element_found=False）"""
        from app.services.execution.actions.interaction import WaitAction

        await self.page.set_content("<html><body></body></html>")

        action = WaitAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=WaitParams(selector="#not-exists", timeout=1000),
        )

        result = await action.execute()
        assert result.success
        assert result.data.element_found is False

    @pytest.mark.asyncio(loop_scope="session")
    async def test_wait_without_selector(self):
        """测试无 selector 时固定等待"""
        from app.services.execution.actions.interaction import WaitAction

        action = WaitAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=WaitParams(timeout=100),
        )

        result = await action.execute()
        assert result.success
        assert result.data.element_found is True


class TestGetTextAction:
    """获取元素文本测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_get_text_single_element(self):
        """测试获取单个元素文本"""
        from app.services.execution.actions.interaction import GetTextAction

        await self.page.set_content("<html><body><div id='target'>Hello Text</div></body></html>")

        action = GetTextAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=GetTextParams(selector="#target"),
        )

        result = await action.execute()
        assert result.success
        assert result.data.text == "Hello Text"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_get_text_multiple_elements(self):
        """测试获取多个匹配元素文本（默认换行分隔）"""
        from app.services.execution.actions.interaction import GetTextAction

        await self.page.set_content("<html><body><div class='item'>A</div><div class='item'>B</div></body></html>")

        action = GetTextAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=GetTextParams(selector=".item"),
        )

        result = await action.execute()
        assert result.success
        assert result.data.text == "A\nB"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_get_text_custom_separator(self):
        """测试自定义分隔符"""
        from app.services.execution.actions.interaction import GetTextAction

        await self.page.set_content("<html><body><div class='item'>A</div><div class='item'>B</div></body></html>")

        action = GetTextAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=GetTextParams(selector=".item", separator=","),
        )

        result = await action.execute()
        assert result.success
        assert result.data.text == "A,B"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_get_text_without_selector_fails(self):
        """测试无 selector 时返回失败"""
        from app.services.execution.actions.interaction import GetTextAction

        action = GetTextAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=GetTextParams(),
        )

        result = await action.execute()
        assert not result.success
        assert result.error is not None


class TestGetWindowAction:
    """获取 window 属性测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_get_window_property_path(self):
        """测试通过 property_path 获取 window 属性"""
        from app.services.execution.actions.interaction import GetWindowAction

        await self.page.set_content("<html><head><title>Test Title</title></head><body></body></html>")

        action = GetWindowAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=GetWindowParams(property_path="document.title"),
        )

        result = await action.execute()
        assert result.success
        assert result.data.value == "Test Title"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_get_window_object_name(self):
        """测试通过 object_name 获取 window 对象所有字段"""
        from app.services.execution.actions.interaction import GetWindowAction

        await self.page.set_content("<html><body></body></html>")

        action = GetWindowAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=GetWindowParams(object_name="location"),
        )

        result = await action.execute()
        assert result.success
        assert isinstance(result.data.values, dict)
        # location 对象应包含 href 字段
        assert "href" in result.data.values

    @pytest.mark.asyncio(loop_scope="session")
    async def test_get_window_inner_width(self):
        """测试获取 window.innerWidth 数值属性"""
        from app.services.execution.actions.interaction import GetWindowAction

        await self.page.set_content("<html><body></body></html>")

        action = GetWindowAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=GetWindowParams(property_path="innerWidth"),
        )

        result = await action.execute()
        assert result.success
        # innerWidth 是数字，转字符串后非空
        assert result.data.value != ""