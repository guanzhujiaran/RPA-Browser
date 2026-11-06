"""新增动作冒烟：press → element_state → select_option → wait_for_url → wait_for_response

单个动作的单测已分别覆盖细节，这里串起来验证「同一页面连续执行 + 变量合并」的链路
（与工作流里的真实用法一致：动作之间靠 output_vars 传递结果）。
`wait_for_response` 按计划书断言**超时失败**路径。
"""

import pytest
from playwright.async_api import Page

from app.models.execution.action_params import (
    ElementStateParams,
    PressParams,
    SelectOptionParams,
    WaitForResponseParams,
    WaitForUrlParams,
)
from app.models.execution.enums import ElementStateKindEnum, SelectOptionByEnum

_HTML = """
<input id="kw" />
<select id="sel">
  <option value="v1">One</option>
  <option value="v2">Two</option>
</select>
"""


class TestSmokeNewActions:
    """5 个新动作串行执行"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    async def _new(self, action_cls, params):
        return action_cls.new_action(
            mid=1, page=self.page, variables={}, params=params
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_chain(self):
        from app.services.execution.actions.interaction import (
            ElementStateAction,
            PressAction,
            SelectOptionAction,
        )
        from app.services.execution.actions.navigation import WaitForUrlAction
        from app.services.execution.actions.wait_for_response import (
            WaitForResponseAction,
        )

        await self.page.goto("about:blank")
        await self.page.set_content(_HTML)

        # 1) 按键：聚焦输入框并输入字符
        press = await self._new(
            PressAction, PressParams(selector="#kw", key="a")
        )
        press_result = await press.execute()
        assert press_result.success

        # 2) 读状态：输入框当前值应为 "a"
        state = await self._new(
            ElementStateAction,
            ElementStateParams(selector="#kw", kind=ElementStateKindEnum.INPUT_VALUE),
        )
        state_result = await state.execute()
        assert state_result.success
        assert state_result.data.state_value == "a"

        # 3) 下拉选择：按 value 选中 v2
        select = await self._new(
            SelectOptionAction,
            SelectOptionParams(selector="#sel", by=SelectOptionByEnum.VALUE, values=["v2"]),
        )
        select_result = await select.execute()
        assert select_result.success
        assert select_result.data.selected == ["v2"]

        # 4) 等待 URL：当前已在 about:blank，立即命中
        wait_url = await self._new(
            WaitForUrlAction, WaitForUrlParams(url_pattern="about:blank", timeout=5000)
        )
        assert (await wait_url.execute()).success

        # 5) 等待响应：没有任何请求，按计划书应**超时失败**（与 wait 语义相反）
        wait_resp = await self._new(
            WaitForResponseAction,
            WaitForResponseParams(
                url_pattern="**/smoke-never-requested**", timeout=1000
            ),
        )
        wait_resp_result = await wait_resp.execute()
        assert not wait_resp_result.success
        assert "超时" in (wait_resp_result.error or "")
