"""scroll 动作参数扩展单测（离线，不依赖真实浏览器）

覆盖 ``docs/内置滚动动作参数扩展计划书.md`` 的三种模式：
- 滚轮模式（x/y）：`page.mouse.wheel`，位移精确等于 y，分 steps 次；
- 元素模式（selector）：`locator.scroll_into_view_if_needed`；
- 兜底模式（都不给）：`window.scrollTo(0, 0)`。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.models.execution.action_params import ScrollParams
from app.services.execution.actions.interaction import ScrollAction


class FakeMouse:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def wheel(self, dx: int, dy: int) -> None:
        self.calls.append((dx, dy))


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()
        self.locators: list[str] = []
        self.evaluated: list[str] = []

    def locator(self, selector: str):
        self.locators.append(selector)
        locator = MagicMock()
        locator.scroll_into_view_if_needed = AsyncMock()
        return locator

    async def evaluate(self, script: str):
        self.evaluated.append(script)
        return None


async def run_scroll(params: ScrollParams) -> tuple[FakePage, object]:
    page = FakePage()
    action = ScrollAction.new_action(
        mid=1, page=page, variables={}, params=params
    )
    result = await action.execute()
    return page, result


@pytest.mark.asyncio
async def test_wheel_mode_uses_mouse_wheel():
    page, result = await run_scroll(ScrollParams(x=0, y=900, steps=3, interval_ms=0))

    assert result.success
    assert page.locators == [], "滚轮模式不应走 locator"
    assert page.evaluated == [], "滚轮模式不应走 window.scrollTo"
    assert len(page.mouse.calls) == 3, "应按 steps 分步滚动"
    assert sum(dy for _, dy in page.mouse.calls) == 900, "累计位移必须等于 y"
    assert all(dx == 0 for dx, _ in page.mouse.calls)


@pytest.mark.asyncio
async def test_wheel_mode_remainder_goes_to_last_step():
    page, result = await run_scroll(ScrollParams(y=1000, steps=3, interval_ms=0))

    assert result.success
    assert sum(dy for _, dy in page.mouse.calls) == 1000
    assert page.mouse.calls[-1][1] != page.mouse.calls[0][1], "余数应补到最后一步"


@pytest.mark.asyncio
async def test_wheel_mode_wins_over_selector():
    page, _ = await run_scroll(
        ScrollParams(selector="bili-comments", y=600, steps=1, interval_ms=0)
    )

    assert page.mouse.calls == [(0, 600)]
    assert page.locators == [], "同时给 selector 时滚轮模式优先"


@pytest.mark.asyncio
async def test_element_mode_unchanged():
    page, result = await run_scroll(ScrollParams(selector="#target"))

    assert result.success
    assert page.locators == ["#target"]
    assert page.mouse.calls == []


@pytest.mark.asyncio
async def test_legacy_mode_scrolls_to_top():
    page, result = await run_scroll(ScrollParams())

    assert result.success
    assert page.evaluated == ["window.scrollTo(0, 0)"]
    assert page.mouse.calls == []
    assert page.locators == []


def test_param_bounds():
    with pytest.raises(ValidationError):
        ScrollParams(steps=0)
    with pytest.raises(ValidationError):
        ScrollParams(steps=21)
    with pytest.raises(ValidationError):
        ScrollParams(interval_ms=99999)
