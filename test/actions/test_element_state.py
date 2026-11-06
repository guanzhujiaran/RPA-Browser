"""element_state 动作单测（本地 HTML，不联网）"""

import pytest
from playwright.async_api import Page

from app.models.execution.action_params import ElementStateParams
from app.models.execution.enums import ElementStateKindEnum, MissingPolicyEnum

_HTML = """
<div id="box" data-role="card">card</div>
<div id="hidden" style="display:none">h</div>
<input id="inp" value="hello" />
<button id="dis" disabled>d</button>
<span class="item">1</span>
<span class="item">2</span>
"""


class TestElementStateAction:
    """元素状态读取"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    async def _prepare(self) -> None:
        await self.page.goto("about:blank")
        await self.page.set_content(_HTML)

    async def _run(self, **params) -> object:
        from app.services.execution.actions.interaction import ElementStateAction

        action = ElementStateAction.new_action(
            mid=1, page=self.page, variables={}, params=params
        )
        return await action.execute()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_kind_attribute(self):
        await self._prepare()
        result = await self._run(
            selector="#box", kind=ElementStateKindEnum.ATTRIBUTE, attribute="data-role"
        )
        assert result.success
        assert result.data.state_value == "card"
        assert result.data.state_type == "string"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_kind_count(self):
        await self._prepare()
        result = await self._run(selector=".item", kind=ElementStateKindEnum.COUNT)
        assert result.success
        assert result.data.state_value == 2
        assert result.data.state_type == "number"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_kind_visible(self):
        await self._prepare()
        visible = await self._run(selector="#box", kind=ElementStateKindEnum.VISIBLE)
        hidden = await self._run(selector="#hidden", kind=ElementStateKindEnum.VISIBLE)
        assert visible.data.state_value is True
        assert hidden.data.state_value is False
        assert visible.data.state_type == "boolean"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_kind_enabled(self):
        await self._prepare()
        disabled = await self._run(selector="#dis", kind=ElementStateKindEnum.ENABLED)
        enabled = await self._run(selector="#box", kind=ElementStateKindEnum.ENABLED)
        assert disabled.data.state_value is False
        assert enabled.data.state_value is True

    @pytest.mark.asyncio(loop_scope="session")
    async def test_kind_input_value(self):
        await self._prepare()
        result = await self._run(selector="#inp", kind=ElementStateKindEnum.INPUT_VALUE)
        assert result.success
        assert result.data.state_value == "hello"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_ignore_returns_empty(self):
        """missing=ignore（默认）时元素不存在按空值成功返回（探针语义）"""
        await self._prepare()
        string_result = await self._run(
            selector="#nope", kind=ElementStateKindEnum.ATTRIBUTE, attribute="data-role"
        )
        count_result = await self._run(
            selector="#nope", kind=ElementStateKindEnum.COUNT
        )
        bool_result = await self._run(
            selector="#nope", kind=ElementStateKindEnum.VISIBLE
        )

        assert string_result.success and string_result.data.state_value == ""
        assert count_result.success and count_result.data.state_value == 0
        assert bool_result.success and bool_result.data.state_value is False

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_fail_raises(self):
        """missing=fail 时元素不存在直接失败"""
        await self._prepare()
        result = await self._run(
            selector="#nope",
            kind=ElementStateKindEnum.COUNT,
            missing=MissingPolicyEnum.FAIL,
        )
        assert not result.success
        assert "#nope" in (result.error or "")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_attribute_required(self):
        """kind=attribute 缺 attribute 时参数校验失败"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ElementStateParams(selector="#box", kind=ElementStateKindEnum.ATTRIBUTE)

        await self._prepare()
        result = await self._run(selector="#box", kind=ElementStateKindEnum.ATTRIBUTE)
        assert not result.success
