"""复合动作执行链路（control_flow.py）的失败语义测试

覆盖与工作流执行链路（pipeline.py）对齐后的行为：

1. 步骤失败（重试耗尽）后执行兜底分支 ``on_error_branch``——之前复合动作
   路径完全不解析该字段，用户配的「失败回退 / 告警」永远不执行；
2. ``on_error=continue`` 时失败**不中断**后续步骤（被容忍的失败不参与分支
   失败判定）——例如「LLM 生成失败 → 继续走后面的抄评论兜底分支」；
3. ``on_error=stop``（默认）仍然中断后续步骤，行为不变。
"""

import pytest

from app.models.execution.action_params import (
    _ensure_action_type,
    workflow_step_adapter,
)
from app.models.execution.builtin_action_types import OnErrorEnum
from app.services.execution.actions.base import ActionResult
from app.services.execution.actions.control_flow import (
    ExecutionContext,
    IfElseAction,
    StepExecutor,
)


def _make_step(
    action_id: str,
    *,
    on_error: str = "stop",
    on_error_branch: list[dict] | None = None,
) -> "WorkflowStep":  # noqa: F821 - 仅用于类型标注
    payload: dict = {
        "action_id": action_id,
        "action_type": action_id,
        "params": {"message": "x"},
        "on_error": on_error,
    }
    if on_error_branch is not None:
        payload["on_error_branch"] = on_error_branch
    return workflow_step_adapter.validate_python(_ensure_action_type(payload))


class _StubExecutor(StepExecutor):
    """按 action_id 决定成败的执行器替身"""

    def __init__(self, failing_action_ids: set[str]):
        super().__init__(ExecutionContext({}), page=None, mid=1)
        self.failing = failing_action_ids
        self.calls: list[str] = []

    async def execute(self, step, step_index: int = 0) -> ActionResult:
        self.calls.append(step.action_id)
        if step.action_id in self.failing:
            return ActionResult(
                success=False, error="boom", action_id=step.action_id
            )
        return ActionResult(success=True, action_id=step.action_id)


def _strategy(executor: StepExecutor) -> IfElseAction:
    """构造一个只用于调用分支内步骤执行逻辑的 IfElseAction"""
    return IfElseAction.new_action(
        mid=1,
        page=None,
        variables={},
        params={"TrueBranch": [], "FalseBranch": []},
    )


@pytest.mark.asyncio
async def test_error_branch_runs_after_failure():
    """失败后执行兜底分支，并把 last_error 注入变量池"""
    executor = _StubExecutor(failing_action_ids={"llm"})
    step = _make_step(
        "llm",
        on_error="continue",
        on_error_branch=[
            {"action_id": "print", "action_type": "print", "params": {"message": "兜底"}}
        ],
    )

    results = await _strategy(executor)._execute_steps_with_context(executor, [step])

    assert "print" in executor.calls
    assert executor.context.variables["last_error"] == "boom"


@pytest.mark.asyncio
async def test_continue_keeps_running_following_steps():
    """on_error=continue ⇒ 失败不中断后续步骤"""
    executor = _StubExecutor(failing_action_ids={"llm"})
    steps = [
        _make_step("llm", on_error="continue"),
        _make_step("print", on_error="continue"),
    ]

    results = await _strategy(executor)._execute_steps_with_context(executor, steps)

    assert executor.calls == ["llm", "print"]
    assert len(results) == 2


@pytest.mark.asyncio
async def test_stop_still_interrupts_following_steps():
    """on_error=stop（默认）⇒ 仍然中断后续步骤（行为不变）"""
    executor = _StubExecutor(failing_action_ids={"llm"})
    steps = [
        _make_step("llm"),
        _make_step("print", on_error="continue"),
    ]

    results = await _strategy(executor)._execute_steps_with_context(executor, steps)

    assert executor.calls == ["llm"]
    assert len(results) == 1


@pytest.mark.asyncio
async def test_tolerated_failure_marked_in_branch_results():
    """被 continue 容忍的失败在分支结果里标记 tolerated（不参与失败判定）"""
    executor = _StubExecutor(failing_action_ids={"llm"})
    step = _make_step("llm", on_error="continue")

    results = await _strategy(executor)._execute_steps_with_context(executor, [step])

    assert results[0]["success"] is False
    assert results[0]["tolerated"] is True
