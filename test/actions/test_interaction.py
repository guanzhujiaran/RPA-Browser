"""
测试交互操作
"""

import time

import pytest
from playwright.async_api import Page

from app.models.execution.action_params import (
    ClickParams,
    InputParams,
    ScrollParams,
    HoverParams,
    WaitParams,
    GetTextParams,
    GetWindowParams,
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

        await self.page.set_content(
            "<html><body><button id='btn' onclick=\"window.__clicked=true\">Click</button></body></html>"
        )

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

        await self.page.set_content(
            "<html><body><button id='btn' onclick=\"var c=parseInt(this.innerText||'0');this.innerText=(c+1).toString()\">0</button></body></html>"
        )

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

        await self.page.set_content(
            "<html><body><button id='btn' oncontextmenu=\"window.__rightClicked=true;return false;\">Right Click</button></body></html>"
        )

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

        await self.page.set_content(
            "<html><body><input id='input' type='text'></body></html>"
        )

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
        assert actual_value == "Hello World", (
            f"Expected 'Hello World', got '{actual_value}'"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_trim(self):
        """trim=true 时去掉首尾空白/换行

        2026-11-06 那轮：评论变量是模型输出，带前导换行，B 站评论框会判空内容
        导致发布无效（输入框仍保留文本），这里保证输入前已规范化。
        """
        from app.services.execution.actions.interaction import InputAction

        await self.page.set_content(
            "<html><body><textarea id='box'></textarea></body></html>"
        )

        action = InputAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params=InputParams(
                selector="#box", value="\n  希望中奖  \n", trim=True
            ),
        )

        result = await action.execute()
        assert result.success
        assert result.data.value_length == len("希望中奖")
        assert await self.page.input_value("#box") == "希望中奖"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_trim_disabled_keeps_value(self):
        from app.services.execution.actions.interaction import InputAction

        await self.page.set_content(
            "<html><body><textarea id='box'></textarea></body></html>"
        )

        action = InputAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params=InputParams(selector="#box", value="  保留空格  "),
        )

        result = await action.execute()
        assert result.success
        assert await self.page.input_value("#box") == "  保留空格  "

    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_without_selector(self):
        """测试无 focus 时直接输入到已聚焦元素"""
        from app.services.execution.actions.interaction import InputAction

        await self.page.set_content(
            "<html><body><input id='input' type='text'></body></html>"
        )
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

        await self.page.set_content(
            "<html><body><input id='input' type='text' value='old'></body></html>"
        )

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
    async def test_scroll_without_params_returns_to_top(self):
        """测试无参数滚动：先滚到底部，再滚回页面顶部

        契约（见 docs/内置滚动动作参数扩展计划书.md）：不提供 selector / x / y 时
        走兜底模式 `window.scrollTo(0, 0)`，即**滚回顶部**（历史行为，存量 JSON 不变）。
        """
        from app.services.execution.actions.interaction import ScrollAction

        await self.page.set_content(
            "<html><body><div style='height: 3000px;'>Long Content</div></body></html>"
        )
        # 先手动滚下去，制造“不在顶部”的前提
        await self.page.evaluate("window.scrollTo(0, 1500)")
        assert await self.page.evaluate("window.scrollY") > 0

        action = ScrollAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ScrollParams(),
        )

        result = await action.execute()
        assert result.success
        # 兜底模式：滚回页面顶部
        scroll_y = await self.page.evaluate("window.scrollY")
        assert scroll_y == 0, f"Expected scrollY == 0 after scroll to top, got {scroll_y}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_scroll_wheel_mode_scrolls_down(self):
        """测试滚轮模式：提供 y 时分步 mouse.wheel 向下滚动（与站点无关的通用能力）"""
        from app.services.execution.actions.interaction import ScrollAction

        await self.page.set_content(
            "<html><body><div style='height: 3000px;'>Long Content</div></body></html>"
        )
        await self.page.evaluate("window.scrollTo(0, 0)")

        action = ScrollAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ScrollParams(y=800, steps=4, interval_ms=50),
        )

        result = await action.execute()
        assert result.success
        scroll_y = await self.page.evaluate("window.scrollY")
        assert scroll_y > 0, f"Expected scrollY > 0 after wheel scroll, got {scroll_y}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_scroll_to_element(self):
        """测试滚动到指定元素"""
        from app.services.execution.actions.interaction import ScrollAction

        await self.page.set_content(
            "<html><body><div id='target' style='margin-top: 2000px;'>Target</div></body></html>"
        )

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

    @pytest.mark.asyncio(loop_scope="session")
    async def test_scroll_multiple_elements_uses_first(self):
        """选择器命中多个元素时滚动首个

        回归：`scroll_into_view_if_needed` 是单元素 API，多元素会因严格模式直接抛错，
        滚动一次都不会发生。列表容器（一条数据一个节点）场景只处理首个元素即可。
        """
        from app.services.execution.actions.interaction import ScrollAction

        await self.page.set_content(
            "<html><body>"
            + "".join(
                f"<div class='item' style='margin-top: 800px;'>item {index}</div>"
                for index in range(5)
            )
            + "</body></html>"
        )

        action = ScrollAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=ScrollParams(selector=".item"),
        )

        result = await action.execute()
        assert result.success
        assert result.error is None, f"多元素不应走异常兜底: {result.error}"
        # 首个元素被滚进视野即视为完成
        is_visible = await self.page.evaluate(
            """
            () => {
                const el = document.querySelectorAll('.item')[0];
                const rect = el.getBoundingClientRect();
                return rect.top >= 0 && rect.bottom <= window.innerHeight;
            }
            """
        )
        assert is_visible, "首个元素应已被滚入视野"


class TestHoverAction:
    """悬停操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_hover_element(self):
        """测试悬停元素"""
        from app.services.execution.actions.interaction import HoverAction

        await self.page.set_content(
            "<html><body><div id='target' style='width: 50px; height: 50px; background: green;'>Hover</div></body></html>"
        )

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

        await self.page.set_content(
            "<html><body><div style='width: 100px; height: 100px; background: blue;'>Area</div></body></html>"
        )

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

        await self.page.set_content(
            "<html><body><div id='target'>Target</div></body></html>"
        )

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

    @pytest.mark.asyncio(loop_scope="session")
    async def test_wait_multiple_elements_visible(self):
        """选择器命中多个元素时按「任一出现」等待

        回归：Playwright 单元素 API（`wait_for`）默认严格模式，多元素会直接抛
        `strict mode violation`，导致等待一次都没生效（历史 bug：评论区
        `bili-comment-thread-renderer` 命中 20 个元素）。这里 20 个 `.item`
        必须正常等到可见，而不是走异常兜底。
        """
        from app.services.execution.actions.interaction import WaitAction

        await self.page.set_content(
            "<html><body>"
            + "".join(f"<div class='item'>item {index}</div>" for index in range(20))
            + "</body></html>"
        )

        action = WaitAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=WaitParams(selector=".item", timeout=5000),
        )

        result = await action.execute()
        assert result.success
        assert result.data.element_found is True
        assert result.error is None, f"多元素不应走异常兜底: {result.error}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_wait_hidden_requires_all_gone(self):
        """state=hidden 默认「全部消失」：逐个移除过程中不能提前返回"""
        from app.services.execution.actions.interaction import WaitAction
        from app.models.execution.enums import ElementStateEnum

        await self.page.set_content(
            "<html><body>"
            "<div class='item' id='a'>A</div>"
            "<div class='item' id='b'>B</div>"
            "</body></html>"
        )
        await self.page.evaluate(
            """
            () => {
                setTimeout(() => document.querySelector('#a').remove(), 150);
                setTimeout(() => document.querySelector('#b').remove(), 300);
            }
            """
        )

        started = time.monotonic()
        action = WaitAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=WaitParams(
                selector=".item",
                state=ElementStateEnum.HIDDEN,
                timeout=5000,
            ),
        )

        result = await action.execute()
        elapsed = time.monotonic() - started
        assert result.success
        assert result.data.element_found is True
        assert elapsed >= 0.25, f"仍有元素可见时不应返回，实际耗时 {elapsed:.2f}s"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_wait_hidden_match_any_returns_early(self):
        """match=any + state=hidden：任一元素消失即返回"""
        from app.services.execution.actions.interaction import WaitAction
        from app.models.execution.enums import ElementStateEnum, WaitMatchEnum

        await self.page.set_content(
            "<html><body>"
            "<div class='item'>A</div>"
            "<div class='item' style='display: none'>B</div>"
            "</body></html>"
        )

        started = time.monotonic()
        action = WaitAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=WaitParams(
                selector=".item",
                state=ElementStateEnum.HIDDEN,
                match=WaitMatchEnum.ANY,
                timeout=5000,
            ),
        )

        result = await action.execute()
        elapsed = time.monotonic() - started
        assert result.success
        assert result.data.element_found is True
        assert elapsed < 2, f"任一元素已隐藏应立即返回，实际耗时 {elapsed:.2f}s"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_wait_hidden_all_times_out_when_one_remains(self):
        """state=hidden 全部语义下，只要还有一个元素可见就按超时处理（success 但 found=False）"""
        from app.services.execution.actions.interaction import WaitAction
        from app.models.execution.enums import ElementStateEnum

        await self.page.set_content(
            "<html><body><div class='item'>A</div><div class='item'>B</div></body></html>"
        )

        action = WaitAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=WaitParams(
                selector=".item",
                state=ElementStateEnum.HIDDEN,
                timeout=600,
            ),
        )

        result = await action.execute()
        assert result.success
        assert result.data.element_found is False
        assert result.logs and "等待超时" in result.logs[0]


class TestGetTextAction:
    """获取元素文本测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_get_text_single_element(self):
        """测试获取单个元素文本"""
        from app.services.execution.actions.interaction import GetTextAction

        await self.page.set_content(
            "<html><body><div id='target'>Hello Text</div></body></html>"
        )

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

        await self.page.set_content(
            "<html><body><div class='item'>A</div><div class='item'>B</div></body></html>"
        )

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

        await self.page.set_content(
            "<html><body><div class='item'>A</div><div class='item'>B</div></body></html>"
        )

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

        await self.page.set_content(
            "<html><head><title>Test Title</title></head><body></body></html>"
        )

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
