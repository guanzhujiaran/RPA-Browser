"""wait_for_response 动作单测（本地路由拦截，不联网）

用 `page.route` 拦截请求并 fulfill，不需要任何外部服务；
这样能拿到真实的 Playwright `Response` 对象（状态码 / URL / 响应体）。
"""

import asyncio

import pytest
from playwright.async_api import Page, Route

from app.models.execution.action_params import WaitForResponseParams
from app.models.execution.enums import HttpMethodEnum, UrlPatternTypeEnum


class TestWaitForResponseAction:
    """等待 HTTP 响应"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    async def _prepare(self) -> None:
        """注册路由（GET→201 / POST→202）+ 清空页面，确保用例互不影响。"""

        async def _handler(route: Route) -> None:
            # GET 与 POST 返回不同状态码，用于验证 method 过滤真的生效
            if route.request.method.upper() == "POST":
                await route.fulfill(status=202, body="pong-post")
            else:
                await route.fulfill(status=201, body="pong-get")

        await self.page.unroute_all()
        await self.page.route("**/api/ping", _handler)
        await self.page.goto("about:blank")

    async def _start(self, **params):
        from app.services.execution.actions.wait_for_response import (
            WaitForResponseAction,
        )

        action = WaitForResponseAction.new_action(
            mid=1, page=self.page, variables={}, params=params
        )
        task = asyncio.create_task(action.execute())
        # 让动作先进入「注册响应监听」状态，再触发请求（避免先命中后注册的竞态）
        await asyncio.sleep(0.3)
        return task

    @pytest.mark.asyncio(loop_scope="session")
    async def test_hit_returns_status_and_body(self):
        """命中后返回状态码 / 是否成功 / URL / 响应体文本（字符串，不是 dict）"""
        await self._prepare()
        task = await self._start(
            url_pattern="**/api/ping", include_body=True, timeout=5000
        )
        await self.page.set_content(
            '<script>setTimeout(() => fetch("https://mock.local/api/ping"), 50)</script>'
        )
        result = await task

        assert result.success
        assert result.data.response_status == 201
        assert result.data.response_ok is True
        assert "api/ping" in result.data.response_url
        assert result.data.response_text == "pong-get"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_timeout_fails(self):
        """超时即失败（与 wait 的「超时也成功」语义相反）"""
        await self._prepare()
        result = await (
            await self._start(url_pattern="**/api/nothing-here", timeout=1000)
        )

        assert not result.success
        assert "超时" in (result.error or "")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_method_filter_skips_non_matching(self):
        """先到的 GET 不匹配 method=POST 时继续等待，最终拿到 POST 的响应"""
        await self._prepare()
        task = await self._start(
            url_pattern="**/api/ping", method=HttpMethodEnum.POST, timeout=5000
        )
        await self.page.set_content(
            "<script>"
            'setTimeout(() => fetch("https://mock.local/api/ping"), 30);'
            'setTimeout(() => fetch("https://mock.local/api/ping", {method: "POST"}), 200);'
            "</script>"
        )
        result = await task

        assert result.success
        # GET 打的是 201，POST 打的是 202：拿到 202 说明确实按 method 过滤了
        assert result.data.response_status == 202

    @pytest.mark.asyncio(loop_scope="session")
    async def test_regex_pattern(self):
        """regex 模式匹配响应 URL"""
        await self._prepare()
        task = await self._start(
            url_pattern=r".*/api/ping$",
            pattern_type=UrlPatternTypeEnum.REGEX,
            timeout=5000,
        )
        await self.page.set_content(
            '<script>setTimeout(() => fetch("https://mock.local/api/ping"), 50)</script>'
        )
        result = await task

        assert result.success
        assert result.data.response_status == 201

    @pytest.mark.asyncio(loop_scope="session")
    async def test_include_body_default_off(self):
        """include_body 默认关闭：不读响应体，response_text 为空串"""
        await self._prepare()
        task = await self._start(url_pattern="**/api/ping", timeout=5000)
        await self.page.set_content(
            '<script>setTimeout(() => fetch("https://mock.local/api/ping"), 50)</script>'
        )
        result = await task

        assert result.success
        assert result.data.response_text == ""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_url_pattern_fails(self):
        """url_pattern 必填"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WaitForResponseParams(url_pattern="")

        await self._prepare()
        result = await (await self._start(url_pattern="", timeout=1000))
        assert not result.success

    # ── trigger_selector：把「点击」放进等待窗口，从根上消除注册过晚的竞态 ──

    @pytest.mark.asyncio(loop_scope="session")
    async def test_trigger_selector_captures_response(self):
        """由本动作自己点击触发请求：注册在前、触发在后，必定捕获"""
        from app.services.execution.actions.wait_for_response import (
            WaitForResponseAction,
        )

        await self._prepare()
        # 点击后立刻发请求（响应很快，模拟"click 内部就回收"的极端情况）
        await self.page.set_content(
            """
            <button id="go">提交</button>
            <script>
              document.getElementById('go').addEventListener('click', () => {
                fetch('https://mock.local/api/ping', {method: 'POST'});
              });
            </script>
            """
        )

        action = WaitForResponseAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params=WaitForResponseParams(
                url_pattern="**/api/ping",
                method=HttpMethodEnum.POST,
                include_body=True,
                trigger_selector="#go",
                timeout=5000,
            ),
        )
        result = await action.execute()

        assert result.success
        assert result.data.response_status == 202
        assert result.data.response_text == "pong-post"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_trigger_click_failure_is_distinguishable(self):
        """触发元素不可点时，错误信息应指向「点击失败」而不是「等待超时」"""
        from app.services.execution.actions.wait_for_response import (
            WaitForResponseAction,
        )

        await self._prepare()
        await self.page.set_content("<div>no button here</div>")

        action = WaitForResponseAction.new_action(
            mid=1,
            page=self.page,
            variables={},
            params=WaitForResponseParams(
                url_pattern="**/api/ping",
                trigger_selector="#not-exist",
                trigger_timeout=1000,
                timeout=5000,
            ),
        )
        result = await action.execute()

        assert not result.success
        assert "触发点击失败" in (result.error or "")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_trigger_timeout_bounds(self):
        """trigger_timeout 与 timeout 同口径（>=1000ms）"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WaitForResponseParams(url_pattern="**/api/ping", trigger_timeout=100)
