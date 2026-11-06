"""select_option 动作单测（本地 HTML，不联网）"""

import pytest
from playwright.async_api import Page

from app.models.execution.action_params import SelectOptionParams
from app.models.execution.enums import SelectOptionByEnum

_HTML = """
<select id="s">
  <option value="a">Apple</option>
  <option value="b">Banana</option>
  <option value="c">Cherry</option>
</select>
"""


class TestSelectOptionAction:
    """原生下拉选择"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    async def _prepare(self) -> None:
        await self.page.goto("about:blank")
        await self.page.set_content(_HTML)

    async def _run(self, **params) -> object:
        from app.services.execution.actions.interaction import SelectOptionAction

        action = SelectOptionAction.new_action(
            mid=1, page=self.page, variables={}, params=params
        )
        return await action.execute()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_select_by_value(self):
        await self._prepare()
        result = await self._run(
            selector="#s", by=SelectOptionByEnum.VALUE, values=["b"]
        )
        assert result.success
        assert result.data.selected == ["b"]
        assert result.data.selected_count == 1

    @pytest.mark.asyncio(loop_scope="session")
    async def test_select_by_label(self):
        await self._prepare()
        result = await self._run(
            selector="#s", by=SelectOptionByEnum.LABEL, values=["Cherry"]
        )
        assert result.success
        assert result.data.selected == ["c"]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_select_by_index(self):
        await self._prepare()
        result = await self._run(
            selector="#s", by=SelectOptionByEnum.INDEX, values=["0"]
        )
        assert result.success
        assert result.data.selected == ["a"]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_select_by_index_invalid(self):
        """by=index 时 values 必须是整数下标，非法值给出可读错误"""
        await self._prepare()
        result = await self._run(
            selector="#s", by=SelectOptionByEnum.INDEX, values=["not-a-number"]
        )
        assert not result.success
        assert "整数下标" in (result.error or "")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_empty_values_rejected(self):
        """values 至少 1 个，空列表在参数校验阶段被拒"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SelectOptionParams(selector="#s", values=[])

        await self._prepare()
        result = await self._run(
            selector="#s", by=SelectOptionByEnum.VALUE, values=[]
        )
        assert not result.success
