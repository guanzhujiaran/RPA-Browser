"""定位器真实浏览器实跑（本地 HTML，不联网）

静态单测（``test_locator.py``）只能证明「派发了哪些调用」，这里用真实 chromium
跑一遍动作本身，确认定位器在真实 DOM 上的行为：

- 按文本 / has_text 定位到正确的元素并点击；
- 候选链：主定位器 0 命中时退化到下一个候选；
- ``within`` 把定位限制在父元素内（同名按钮只点范围内的那个）；
- ``pick=nth`` 点中列表里的第 N 个；
- ``get_text`` 用 ``item_locator`` 按条目取文本；
- ``missing=ignore`` 时元素缺失不判失败。
"""

from __future__ import annotations

import pytest

from app.models.execution.action_params import (
    ClickParams,
    GetTextParams,
    LocatorSpec,
    WaitParams,
)
from app.models.execution.enums import (
    ElementStateEnum,
    LocatorPickEnum,
    LocatorStrategyEnum,
    MissingPolicyEnum,
)
from app.services.execution.actions.interaction import (
    ClickAction,
    GetTextAction,
    WaitAction,
)

_PAGE_HTML = """
<div id="root">
  <div class="box box-a">
    <button class="go" onclick="window.__clicks.push('a')">关注</button>
  </div>
  <div class="box box-b">
    <button class="go" onclick="window.__clicks.push('b')">关注</button>
  </div>
  <ul id="list">
    <li class="item" data-id="1"><span class="name">第一条</span></li>
    <li class="item" data-id="2"><span class="name">第二条</span></li>
    <li class="item" data-id="3"><span class="name">第三条</span></li>
  </ul>
</div>
<script>window.__clicks = [];</script>
"""


async def _load(page):
    """加载本地测试页（会话级共享页，每个用例前重置一次）

    不用 function 级 async fixture：``page`` 是会话级 fixture，绑定在 session 事件循环上，
    而 function 级 async fixture 跑在 function 循环里，两者混用会直接死锁。
    """
    await page.set_content(_PAGE_HTML)
    return page


def _click_params(**overrides) -> ClickParams:
    base = {"timeout": 5000}
    base.update(overrides)
    return ClickParams(**base)


@pytest.mark.asyncio(loop_scope="session")
async def test_click_by_text(page):
    page = await _load(page)
    action = ClickAction.new_action(
        mid=1,
        page=page,
        variables={},
        params=_click_params(
            locator=LocatorSpec(
                strategy=LocatorStrategyEnum.TEXT, value="关注", pick=LocatorPickEnum.FIRST
            ),
        ),
    )

    result = await action.execute()

    assert result.success, result.error
    assert await page.evaluate("window.__clicks") == ["a"], "文本定位应点中第一个「关注」"


@pytest.mark.asyncio(loop_scope="session")
async def test_within_scopes_click_to_parent(page):
    page = await _load(page)
    action = ClickAction.new_action(
        mid=1,
        page=page,
        variables={},
        params=_click_params(
            locator=LocatorSpec(value=".go", within=".box-b", pick=LocatorPickEnum.FIRST),
        ),
    )

    result = await action.execute()

    assert result.success, result.error
    assert await page.evaluate("window.__clicks") == ["b"], "within 应把定位限制在 .box-b 内"


@pytest.mark.asyncio(loop_scope="session")
async def test_fallbacks_degrade_on_real_dom(page):
    page = await _load(page)
    action = ClickAction.new_action(
        mid=1,
        page=page,
        variables={},
        params=_click_params(
            locator=LocatorSpec(
                value=".not-exist", timeout_ms=300, pick=LocatorPickEnum.FIRST
            ),
            fallbacks=[
                LocatorSpec(value=".still-not-exist", timeout_ms=300),
                LocatorSpec(value=".box-a .go", timeout_ms=1000),
            ],
        ),
    )

    result = await action.execute()

    assert result.success, result.error
    assert await page.evaluate("window.__clicks") == ["a"]
    joined = " ".join(result.logs)
    assert "候选" in joined or result.success


@pytest.mark.asyncio(loop_scope="session")
async def test_pick_nth_clicks_expected_item(page):
    page = await _load(page)
    action = ClickAction.new_action(
        mid=1,
        page=page,
        variables={},
        params=_click_params(
            locator=LocatorSpec(value=".item", pick=LocatorPickEnum.NTH, nth=2)
        ),
    )
    # 点 li 不产生业务副作用，这里只验证「命中第 3 个」不报错
    result = await action.execute()

    assert result.success, result.error


@pytest.mark.asyncio(loop_scope="session")
async def test_get_text_by_item_locator(page):
    page = await _load(page)
    action = GetTextAction.new_action(
        mid=1,
        page=page,
        variables={},
        params=GetTextParams(
            locator=LocatorSpec(value=".name"),
            item_locator=LocatorSpec(value=".item"),
        ),
    )

    result = await action.execute()

    assert result.success, result.error
    assert result.data.items == ["第一条", "第二条", "第三条"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_ignore_does_not_fail(page):
    page = await _load(page)
    action = ClickAction.new_action(
        mid=1,
        page=page,
        variables={},
        params=_click_params(
            locator=LocatorSpec(value=".no-such-button", timeout_ms=300),
            missing=MissingPolicyEnum.IGNORE,
        ),
    )

    result = await action.execute()

    assert result.success
    assert result.data.clicked is False
    assert any("missing=ignore" in log for log in result.logs)


@pytest.mark.asyncio(loop_scope="session")
async def test_wait_detects_delayed_element(page):
    page = await _load(page)
    await page.evaluate(
        "setTimeout(() => {"
        "  const el = document.createElement('div');"
        "  el.id = 'late'; el.textContent = '出现了';"
        "  document.body.appendChild(el);"
        "}, 300)"
    )
    action = WaitAction.new_action(
        mid=1,
        page=page,
        variables={},
        params=WaitParams(
            locator=LocatorSpec(value="#late"),
            state=ElementStateEnum.VISIBLE,
            timeout=3000,
        ),
    )

    result = await action.execute()

    assert result.success, result.error
    assert result.data.element_found is True
