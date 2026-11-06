"""press 动作单测（本地 HTML，不联网）"""

import pytest
from playwright.async_api import Page

from app.models.execution.action_params import PressParams

_HTML = """
<input id="box" />
<div id="log"></div>
<script>
  document.addEventListener('keydown', (e) => {
    document.getElementById('log').textContent += e.key;
  });
</script>
"""


class TestPressAction:
    """按键动作"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    async def _prepare(self) -> None:
        await self.page.goto("about:blank")
        await self.page.set_content(_HTML)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_press_with_selector_focuses_element(self):
        """提供 selector 时先聚焦该元素再按键（字符进入输入框）"""
        from app.services.execution.actions.interaction import PressAction

        await self._prepare()
        action = PressAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params=PressParams(selector="#box", key="a"),
        )
        result = await action.execute()

        assert result.success
        assert result.data.pressed is True
        assert await self.page.locator("#box").input_value() == "a"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_press_without_selector_uses_page_keyboard(self):
        """无 selector 时把按键发给当前焦点（body 收到 keydown）"""
        from app.services.execution.actions.interaction import PressAction

        await self._prepare()
        action = PressAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params=PressParams(key="Enter"),
        )
        result = await action.execute()

        assert result.success
        assert (await self.page.locator("#log").text_content()) == "Enter"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_press_missing_key_fails(self):
        """缺 key 时参数校验失败（动作返回 success=False）"""
        from app.services.execution.actions.interaction import PressAction

        await self._prepare()
        action = PressAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params={"selector": "", "key": ""},
        )
        result = await action.execute()

        assert not result.success
        assert "key" in (result.error or "")
