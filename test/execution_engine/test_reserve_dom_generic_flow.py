"""预约抽奖「DOM 流程」通用动作步骤的 RPA 项目内实跑测试

背景：`bili_reserve` 站点专属内置动作已移除，其 DOM 流程改由
`actions/action_104682832_参与B站抽奖.json` 里的通用动作步骤表达
（见 `docs/内置动作通用化与图标全覆盖计划书.md` §4.2）。

本测试**直接读取实际发布的动作 JSON**，取出预约循环中的通用 DOM 步骤，
包一层测试工作流后通过 `ExecutionEngine.execute_steps` + 真实 Playwright chromium 跑一遍：
导航 URL 由 `page.route` 拦截并注入本地 HTML，不依赖外网、不依赖 B 站页面。
单测单个动作不算「实跑」，这里跑的是完整 Pipeline（loop / if_else / expression /
break_condition / on_error / 变量写入 / 元素计数 / 点击 / 等待）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from playwright.async_api import Page, Route

from app.models.execution.action_params import workflow_step_adapter
from app.models.execution.request_params import WorkflowExecutionRequest
from app.services.execution.engine import ExecutionEngine

type JsonDict = dict[str, object]

ACTION_JSON_PATH = (
    Path(__file__).resolve().parents[2]
    / "actions"
    / "action_104682832_参与B站抽奖.json"
)
#: 公共域名（DNS 可解析为公网 IP，能通过 NavigateAction 的安全检查）；
#: 实际文档由 page.route 拦截注入，不会发起真实网络请求。
TEST_URL = "https://example.com/rpa-reserve-test"
CARD_TEMPLATE = '<div class="subscribe-section-warp" data-sid="123">{}</div>'


@dataclass(frozen=True, slots=True)
class ReserveScenario:
    """一个本地 HTML 场景及其预期的变量快照"""

    name: str
    html: str
    expected_joined: bool
    expected_value: bool
    expected_found: bool
    expected_already: bool
    expected_action_reserve: str
    #: 页面上实际发生的点击次数（验证重试循环真的跑过，而不只是变量被置位）
    expected_clicks: int | None


CLICK_COUNTER = '<span id="clicks">0</span>'


def _card(button_html: str) -> str:
    return CARD_TEMPLATE.format(f"{CLICK_COUNTER}{button_html}")


def _on_click(js_body: str) -> str:
    return (
        "<script>var n=0;</script>"
        f'<button class="btn" onclick="{js_body}">预约</button>'
    )


SCENARIOS: tuple[ReserveScenario, ...] = (
    ReserveScenario(
        name="未预约-一次点击即生效",
        html=_card(
            _on_click(
                "document.getElementById('clicks').textContent=++n;"
                "this.textContent='已预约'"
            )
        ),
        expected_joined=True,
        expected_value=True,
        expected_found=True,
        expected_already=False,
        expected_action_reserve="已预约",
        expected_clicks=1,
    ),
    ReserveScenario(
        name="首次点击被页面重渲染吞掉-重试后生效",
        html=_card(
            _on_click(
                "document.getElementById('clicks').textContent=++n;"
                "if(n>=2){this.textContent='已预约'}"
            )
        ),
        expected_joined=True,
        expected_value=True,
        expected_found=True,
        expected_already=False,
        expected_action_reserve="已预约",
        expected_clicks=2,
    ),
    ReserveScenario(
        name="已预约-跳过点击",
        html=_card('<button class="btn">已预约</button>'),
        expected_joined=True,
        expected_value=True,
        expected_found=True,
        expected_already=True,
        expected_action_reserve="已预约",
        expected_clicks=0,
    ),
    ReserveScenario(
        name="无预约卡片-跳过本轮",
        html='<div class="empty"></div>',
        expected_joined=False,
        expected_value=False,
        expected_found=False,
        expected_already=False,
        expected_action_reserve="",
        expected_clicks=None,
    ),
)


def _load_dom_steps() -> list[JsonDict]:
    """从实际发布的动作 JSON 中取出「预约抽奖」循环的通用 DOM 步骤

    截止到按 `rs_joined` 记资源结果的 `if_else`（结构化 condition）为止：
    该步骤需要真实工作流上下文，属于测试环境不具备的业务落库环节。
    """
    raw = cast(JsonDict, json.loads(ACTION_JSON_PATH.read_text(encoding="utf-8")))
    steps = cast(list[JsonDict], raw["steps"])
    reserve_loop = next(
        step
        for step in steps
        if step.get("action_id") == "loop"
        and cast(JsonDict, step["params"]).get("loop_items_var")
        == "reserve_data.items"
    )
    body = cast(
        list[JsonDict], cast(JsonDict, reserve_loop["params"])["loopBranch"]
    )

    dom_steps: list[JsonDict] = []
    for step in body:
        params = cast(JsonDict, step["params"])
        if step.get("action_id") == "if_else" and "condition" in params:
            break
        dom_steps.append(step)

    serialized = json.dumps(dom_steps, ensure_ascii=False)
    assert len(dom_steps) >= 5, "JSON 中的通用 DOM 步骤不完整"
    assert "bili_reserve" not in serialized, "动作 JSON 仍引用已移除的站点专属内置动作"
    assert all(step.get("action_type") for step in dom_steps), "步骤缺少 action_type"
    return dom_steps


def _build_test_workflow(reserve_url: str) -> list[JsonDict]:
    """把通用 DOM 步骤包成一条可由引擎直接执行的测试工作流

    末尾的 `print` 放在循环**外**：引擎只返回顶层步骤结果，放外面才能通过
    `ActionResult.variables` 读到循环体写入的变量快照（Scope 不按迭代压栈）。
    """
    dom_steps = _load_dom_steps()
    loop_step: JsonDict = {
        "params": {
            "name": f"测试工作流：预约抽奖 DOM 流程 {reserve_url}",
            "loop_source": "variable",
            "loop_item_var": "rs_item",
            "loop_items_var": "reserve_data.items",
            "loopBranch": dom_steps,
        },
        "action_id": "loop",
        "action_type": "loop",
        "on_error": "continue",
    }
    dump_step: JsonDict = {
        "params": {
            "message": "通用 DOM 流程实跑变量快照",
            "dump_variables": True,
            "dump_max_items": 1,
            "dump_max_length": 2000,
            "log": {"enabled": False},
        },
        "action_id": "print",
        "action_type": "print",
    }
    return [loop_step, dump_step]


async def _serve(route: Route, html: str) -> None:
    await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_reserve_dom_generic_flow(page: Page, scenario: ReserveScenario) -> None:
    """用本地 HTML 实跑动作 JSON 里的通用步骤，校验变量结果与页面交互"""
    async def handler(route: Route) -> None:
        await _serve(route, scenario.html)

    await page.route(TEST_URL, handler)
    try:
        steps = [
            workflow_step_adapter.validate_python(step)
            for step in _build_test_workflow(TEST_URL)
        ]
        req = WorkflowExecutionRequest(
            mid=99900001,
            browser_id=1,
            action_id="composite",
            input_data={},
            output_vars=[],
            variables={
                "reserve_data": {
                    "items": [{"reserve_sid": "123", "reserve_url": TEST_URL}]
                }
            },
        )
        results = await ExecutionEngine().execute_steps(
            req,
            steps=steps,
            session_id="test_reserve_dom",
            browser_id="test_browser",
            page=page,
        )
    finally:
        await page.unroute(TEST_URL, handler)

    assert results, "引擎未返回任何执行结果"
    assert results[-1].success, f"工作流执行失败: {results[-1].error}"
    variables = results[-1].variables or {}
    assert variables.get("rs_joined") is scenario.expected_joined
    assert variables.get("rs_value") is scenario.expected_value
    assert variables.get("rs_found") is scenario.expected_found
    assert variables.get("rs_already") is scenario.expected_already
    assert variables.get("action_reserve") == scenario.expected_action_reserve
    assert int(variables.get("rs_card_count", -1)) == (
        1 if scenario.expected_found else 0
    )
    if scenario.expected_clicks is not None:
        actual_clicks = await page.locator("#clicks").inner_text()
        assert int(actual_clicks.strip()) == scenario.expected_clicks
