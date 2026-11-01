"""「参与B站抽奖」动作的 LLM 编排回归测试（真实动作 JSON）

用 `actions/action_104682832_参与B站抽奖.json` 里 **第三方评论抽奖** 循环的
真实步骤树，验证修复后的编排语义：

- **每一轮都会调用 LLM**（LLM 失败不会让后续轮次跳过 LLM）；
- LLM 失败 ⇒ 该轮走「抄评论」兜底（``random_pick``）；
- LLM 成功 ⇒ 该轮**不**抄评论；
- LLM 失败 ⇒ 该轮的 ``comment_text`` 为空（不会沿用上一轮的旧评论）。

执行器是模拟的：只对 ``llm`` / ``random_pick`` 建模，其余动作一律成功，
因此本测试只关心**控制流走向**，不碰浏览器。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from app.models.execution.action_params import (
    _ensure_action_type,
    workflow_step_adapter,
)
from app.services.execution.actions.base import ActionResult, BaseAction
from app.services.execution.pipeline import PipelineBuilder
from app.services.execution.scope import Scope

_ACTION_JSON = (
    Path(__file__).resolve().parents[2]
    / "actions"
    / "action_104682832_参与B站抽奖.json"
)
#: 第三方评论抽奖循环在动作 steps 里的下标（steps[16]）
_LOTTERY_LOOP_INDEX = 16


def _collect_template_vars(node) -> set[str]:
    """递归收集步骤树里用到的 {{变量}}"""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "params" and isinstance(value, dict):
                for text in _iter_strings(value):
                    found.update(re.findall(r"\{\{([\w.]+)\}\}", text))
            else:
                found.update(_collect_template_vars(value))
    elif isinstance(node, list):
        for item in node:
            found.update(_collect_template_vars(item))
    return found


def _iter_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_strings(item)


def _build_scope(items_count: int = 4) -> Scope:
    """构造一个能跑通整棵步骤树的变量池（模板全部可解析）"""
    variables: dict = {
        "self_nickname": "后藤波奇",
        "up_name": "参半口腔",
        "dyn_content": "正式加入纯过日子人阵营",
        "dyn_comments": "现在用薄荷绿的",
        "llm_model": "openrouter/free",
        "llm_api_key": "sk-test",
        "llm_server_url": "https://openrouter.ai/api/v1",
        "llm_system_prompt": "你是B站普通用户",
        "llm_fallbacks": "[]",
        "others_data": {
            "items": [
                {
                    "dynId": f"dyn-{i}",
                    "dynamicUrl": f"https://www.bilibili.com/opus/dyn-{i}",
                    "up_uid": str(1000 + i),
                }
                for i in range(items_count)
            ],
        },
        # 动作 variables 里声明的初始值（与动作 JSON 的 variables 段一致）
        "comment_text": "",
        "llm_reply_role": "",
        "llm_reply_model": "",
        "editor_text": "",
        "latest_comment_users_text": "",
        "captcha_shown": "false",
        "share_closed": "true",
        "skip_reason": "",
        "action_like": "",
        "is_login": "true",
        "page_ok": "true",
        "record_note": "",
        "last_error": "",
        "already_liked": "",
        "need_like": "",
        "already_followed": "",
        "action_like": "",
        "action_follow": "",
        "action_forward": "",
        # GATE 条件 comment_mode == 'llm'：动作 input_vars 的默认值（工作流 variables 里显式传 llm）
        "comment_mode": "llm",
    }
    return Scope(variables)


class _FakeLlmAction(BaseAction):
    """走真实 ``BaseAction._merge_output_vars`` 的最小 LLM 动作替身

    刻意复用生产代码里的输出变量合并逻辑：动作失败时输出变量是否被置空，
    正是本测试要守住的行为，不能在测试里自己模拟。
    """

    async def _execute(self) -> ActionResult:  # pragma: no cover - 不会被直接调用
        return ActionResult(success=False, error="never executed")


class _FakeExecutor:
    """模拟执行器：只区分 llm / random_pick，其余动作直接成功

    轮次按循环项 ``oth_item.dynId`` 归并——重试发生在同一轮内，不能按调用次数切轮。
    """

    def __init__(self, llm_fail_dyns: set[str]):
        #: 这些动态的 LLM 调用恒失败（按轮次，不是按调用次数）
        self.llm_fail_dyns = llm_fail_dyns
        self.llm_calls = 0
        #: 每轮的事件轨迹（按 dynId 归并）
        self.rounds: dict[str, dict[str, int]] = {}
        #: 每轮 LLM 执行**之后**的 comment_text（失败轮必须为空）
        self.comment_text_after_llm: dict[str, str] = {}

    def _round_of(self, scope) -> dict[str, int]:
        item = scope.get("oth_item") or {}
        dyn_id = str(item.get("dynId"))
        return self.rounds.setdefault(dyn_id, {"llm": 0, "pick": 0, "dyn": dyn_id})

    async def __call__(self, action_id, params, scope, output_vars) -> ActionResult:
        if action_id == "llm":
            self.llm_calls += 1
            current = self._round_of(scope)
            current["llm"] += 1
            action = _FakeLlmAction(
                action_id="llm",
                action_type="llm",
                page=None,
                params={},
                mid=1,
                output_vars=list(output_vars),
                variables=scope.current,
            )
            if self._is_fail_round(scope):
                action._merge_output_vars(
                    ActionResult(success=False, error="429 rate limited")
                )
                self.comment_text_after_llm[current["dyn"]] = str(
                    scope.get("comment_text", "")
                )
                return ActionResult(success=False, error="429 rate limited")
            content = f"LLM 评论 #{self.llm_calls}"
            action._merge_output_vars(
                ActionResult(
                    success=True,
                    data={"content": content, "role": "assistant", "model": "m-x"},
                )
            )
            self.comment_text_after_llm[current["dyn"]] = str(
                scope.get("comment_text", "")
            )
            return ActionResult(
                success=True,
                data={"content": content, "role": "assistant", "model": "m-x"},
            )

        if action_id == "random_pick":
            self._round_of(scope)["pick"] += 1
            picked = "抄来的评论"
            for var_name in output_vars:
                scope.set(var_name, picked)
            return ActionResult(success=True, data={"value": picked})

        return ActionResult(success=True)

    def _is_fail_round(self, scope) -> bool:
        item = scope.get("oth_item") or {}
        return str(item.get("dynId")) in self.llm_fail_dyns


def _load_lottery_loop_step():
    raw = json.loads(_ACTION_JSON.read_text(encoding="utf-8"))
    loop_step = raw["steps"][_LOTTERY_LOOP_INDEX]
    return workflow_step_adapter.validate_python(_ensure_action_type(loop_step))


def _template_vars_are_covered(step) -> None:
    """守住「模板变量必须都有值」：缺一个变量 strict 解析就会失败"""
    raw = json.loads(_ACTION_JSON.read_text(encoding="utf-8"))
    used = _collect_template_vars(raw["steps"][_LOTTERY_LOOP_INDEX])
    scope = _build_scope()
    missing = [
        name
        for name in used
        if "." not in name and scope.get(name, None) is None
    ]
    assert not missing, f"测试变量池缺少模板变量: {missing}"


@pytest.mark.asyncio
async def test_llm_called_every_round_and_fallback_only_on_failure():
    """每轮都调 LLM；LLM 失败的轮次才抄评论"""
    _template_vars_are_covered(None)
    step = _load_lottery_loop_step()
    pipeline = PipelineBuilder.build([step])

    executor = _FakeExecutor(llm_fail_dyns={"dyn-1", "dyn-2"})
    scope = _build_scope(items_count=4)
    await pipeline.execute(scope, executor)

    # 4 条动态 ⇒ 4 轮，每轮都调了 LLM
    assert len(executor.rounds) == 4
    assert all(r["llm"] >= 1 for r in executor.rounds.values())

    # 失败的两轮：动作 JSON 里 LLM 步骤的 retry=0 ⇒ 只调用一次（不重试，
    # 避免 429 额度耗尽时白白多打一次请求），随后触发抄评论兜底
    assert executor.rounds["dyn-1"]["llm"] == 1
    assert executor.rounds["dyn-1"]["pick"] >= 1
    assert executor.rounds["dyn-2"]["llm"] == 1
    assert executor.rounds["dyn-2"]["pick"] >= 1

    # 成功的两轮：不抄评论（直接用 LLM 产出的评论）
    assert executor.rounds["dyn-0"]["llm"] == 1
    assert executor.rounds["dyn-0"]["pick"] == 0
    assert executor.rounds["dyn-3"]["llm"] == 1
    assert executor.rounds["dyn-3"]["pick"] == 0


@pytest.mark.asyncio
async def test_failed_round_does_not_reuse_previous_comment():
    """LLM 失败的轮次 comment_text 必须为空（不能沿用上一轮的旧评论）"""
    _template_vars_are_covered(None)
    step = _load_lottery_loop_step()
    pipeline = PipelineBuilder.build([step])

    executor = _FakeExecutor(llm_fail_dyns={"dyn-1"})
    scope = _build_scope(items_count=3)
    await pipeline.execute(scope, executor)

    # 第 1 轮成功 ⇒ comment_text = LLM 评论 #1
    assert executor.comment_text_after_llm["dyn-0"].startswith("LLM 评论")

    # 第 2 轮 LLM 失败 ⇒ 输出变量必须被置空，不能沿用上一轮的旧评论
    # （不置空的话，这里会是 "LLM 评论 #1"，随后会把上一轮的评论再发一遍）
    assert executor.comment_text_after_llm["dyn-1"] == ""

    # 失败轮走抄评论兜底；成功轮不抄评论
    assert executor.rounds["dyn-0"]["pick"] == 0
    assert executor.rounds["dyn-1"]["pick"] >= 1
