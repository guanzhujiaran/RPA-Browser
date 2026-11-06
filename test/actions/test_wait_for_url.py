"""wait_for_url 动作单测（本地 about:blank，不联网）"""

import pytest
from playwright.async_api import Page

from app.models.execution.action_params import WaitForUrlParams
from app.models.execution.enums import UrlPatternTypeEnum


class TestWaitForUrlAction:
    """等待 URL 匹配"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    async def _prepare(self) -> None:
        await self.page.goto("about:blank")

    async def _run(self, **params) -> object:
        from app.services.execution.actions.navigation import WaitForUrlAction

        action = WaitForUrlAction.new_action(
            mid=1, page=self.page, variables={}, params=params
        )
        return await action.execute()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_glob_hit(self):
        """当前 URL 已匹配 glob 时立即返回"""
        await self._prepare()
        result = await self._run(url_pattern="about:blank", timeout=5000)
        assert result.success
        assert result.data.matched_url.startswith("about:blank")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_regex_hit(self):
        """regex 模式按正则匹配整条 URL"""
        await self._prepare()
        result = await self._run(
            url_pattern=r"about:blank$",
            pattern_type=UrlPatternTypeEnum.REGEX,
            timeout=5000,
        )
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_timeout_fails(self):
        """超时即失败（与 wait 的「超时也成功」相反）"""
        await self._prepare()
        result = await self._run(url_pattern="**/never-match-here**", timeout=1000)
        assert not result.success
        assert "超时" in (result.error or "")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_invalid_regex_fails(self):
        """非法正则给出可读错误而不是底层异常"""
        await self._prepare()
        result = await self._run(
            url_pattern="[unclosed",
            pattern_type=UrlPatternTypeEnum.REGEX,
            timeout=1000,
        )
        assert not result.success
        assert "正则" in (result.error or "")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_pattern_fails(self):
        """url_pattern 必填"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WaitForUrlParams(url_pattern="")

        await self._prepare()
        result = await self._run(url_pattern="", timeout=1000)
        assert not result.success
