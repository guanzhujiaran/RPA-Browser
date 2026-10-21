"""if_else 表达式条件（expression）测试

背景：结构化条件 `condition` 只能与字面量比较，无法表达
「我的昵称是否出现在评论作者列表里」这类**变量之间**的比较。
新增 `expression` 走 `safe_evaluate_condition`（AST 白名单，无 eval）。

见 `docs/条件表达式支持计划书.md`。
"""

import pytest

from app.services.execution.actions.base import ActionResult
from app.services.execution.pipeline import AtomicStep, IfElseStep, Pipeline
from app.services.execution.scope import Scope


async def _run_branch(expression: str, variables: dict) -> list[str]:
    """执行一个 expression 条件的 if_else，返回实际进入的分支标记"""
    hits: list[str] = []

    async def executor(action_id, params, scope, output_vars):
        hits.append(str((params or {}).get("message", "")))
        return ActionResult(success=True, action_id=action_id)

    pipeline = Pipeline(
        steps=[
            IfElseStep(
                action_id="if_else",
                expression=expression,
                true_body=Pipeline(
                    steps=[AtomicStep(action_id="print", params={"message": "TRUE"})]
                ),
                false_body=Pipeline(
                    steps=[AtomicStep(action_id="print", params={"message": "FALSE"})]
                ),
            )
        ]
    )
    await pipeline.execute(Scope(variables), executor, top_level=True)
    return hits


class TestIfElseExpression:
    """表达式条件：变量之间比较"""

    @pytest.mark.asyncio
    async def test_membership_true(self):
        """昵称出现在作者列表 → 走 True 分支（列表成员判断，精确相等）"""
        hits = await _run_branch(
            "self_nickname in latest_comment_users",
            {"self_nickname": "小明", "latest_comment_users": ["别人", "小明"]},
        )
        assert hits == ["TRUE"]

    @pytest.mark.asyncio
    async def test_membership_false(self):
        """昵称不在作者列表 → 走 False 分支"""
        hits = await _run_branch(
            "self_nickname in latest_comment_users",
            {"self_nickname": "小明", "latest_comment_users": ["路人甲", "路人乙"]},
        )
        assert hits == ["FALSE"]

    @pytest.mark.asyncio
    async def test_substring_match_on_joined_text(self):
        """用拼接文本做子串判断：元素文本带空白/后缀也能命中（动作里用的是这种）"""
        hits = await _run_branch(
            "self_nickname in latest_comment_users_text",
            {"self_nickname": "小明", "latest_comment_users_text": "别人\\n小明 Lv6"},
        )
        assert hits == ["TRUE"]

    @pytest.mark.asyncio
    async def test_empty_list_false(self):
        """列表为空（没取到评论）→ False，不误判为成功"""
        hits = await _run_branch(
            "self_nickname in latest_comment_users",
            {"self_nickname": "小明", "latest_comment_users": []},
        )
        assert hits == ["FALSE"]

    @pytest.mark.asyncio
    async def test_variable_compare(self):
        """两个变量直接比较"""
        hits = await _run_branch("a == b", {"a": 3, "b": 3})
        assert hits == ["TRUE"]
        hits = await _run_branch("a == b", {"a": 3, "b": 4})
        assert hits == ["FALSE"]

    @pytest.mark.asyncio
    async def test_undefined_variable_falls_back_to_false(self):
        """变量未定义：表达式求值异常 → 走 False 分支，不抛异常中断运行"""
        hits = await _run_branch("self_nickname in latest_comment_users", {})
        assert hits == ["FALSE"]
