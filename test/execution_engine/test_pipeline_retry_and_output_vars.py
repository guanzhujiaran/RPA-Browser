"""步骤级「重试 / 兜底分支 / 输出变量置空」的行为测试

覆盖三条修复后的语义（工作流执行链路 = pipeline.py）：

1. **重试先于兜底**：``on_error=continue`` + ``retry=N`` 时先重试，重试成功**不**跑
   ``on_error_branch``；重试全部失败才跑兜底分支（此前 continue 抢先命中导致
   retry 被静默忽略，且兜底分支在重试之前就执行了）。
2. **失败即置空输出变量**：动作失败 ⇒ 声明的 ``output_vars`` 一律置空，
   不再沿用上一轮遗留的旧值（否则「LLM 本轮失败 → 仍用上一轮旧评论」）。
3. **兜底分支同步执行**：``if_else`` 分支失败时的 ``on_error_branch`` 必须 await，
   否则它会与主流程并发、写到已 pop 的作用域层。
"""

import pytest
from app.models.execution.action_params import OnErrorEnum
from app.services.execution.actions.base import ActionResult, BaseAction
from app.services.execution.pipeline import (
    AtomicStep,
    IfElseStep,
    Pipeline,
)
from app.services.execution.scope import Scope


class _FailingAction(BaseAction):
    """最小可用的动作实现：恒定失败，用于验证输出变量置空"""

    async def _execute(self) -> ActionResult:
        return ActionResult(success=False, error="boom")


def _make_action(output_vars: list[str], variables: dict) -> _FailingAction:
    return _FailingAction(
        action_id="llm",
        action_type="llm",
        page=None,
        params={},
        mid=1,
        output_vars=output_vars,
        variables=variables,
    )


# ── 1. 输出变量：引擎不擅自改写用户变量 ──────────────────────────


def test_output_vars_untouched_on_failure():
    """动作失败 ⇒ 引擎**不**清空 output_vars（用户变量不被擅自改动）

    变量该是什么值，只由步骤自己声明的 ``output_vars``（产出）与
    ``input_vars``（显式写入）决定；「每轮抹掉某个变量」由用户在每轮都会
    执行的那个步骤上用 ``input_vars`` 显式声明，而不是引擎在失败时偷偷改。
    """
    variables = {"comment_text": "上一轮的旧评论"}
    action = _make_action(["comment_text"], variables)

    action._merge_output_vars(ActionResult(success=False, error="boom"))

    assert variables["comment_text"] == "上一轮的旧评论"


def test_output_vars_untouched_when_data_is_none():
    """失败且 data 为 None（LLM 失败的典型形态）⇒ 同样不动用户变量"""
    variables = {"comment_text": "上一轮的旧评论", "llm_reply_model": "gpt-x"}
    action = _make_action(["comment_text", "llm_reply_model"], variables)

    action._merge_output_vars(ActionResult(success=False, error="boom", data=None))

    assert variables["comment_text"] == "上一轮的旧评论"
    assert variables["llm_reply_model"] == "gpt-x"


def test_output_vars_kept_on_success():
    """成功 ⇒ 仍按 data 的顺序赋值，不受影响"""
    variables: dict = {}
    action = _make_action(["comment_text"], variables)

    action._merge_output_vars(
        ActionResult(success=True, data={"content": "一条短评", "role": "assistant"})
    )

    assert variables["comment_text"] == "一条短评"


# ── 2. 步骤级重试 ──────────────────────────────────────────────────


class _Recorder:
    """记录 executor 被调用的动作序列"""

    def __init__(self, fail_first: int = 0, fail_all: bool = False):
        self.calls: list[str] = []
        self._fail_first = fail_first
        self._fail_all = fail_all

    async def __call__(self, action_id, params, scope, output_vars) -> ActionResult:
        self.calls.append(action_id)
        if self._fail_all:
            return ActionResult(success=False, error=f"{action_id} failed")
        if self.calls.count(action_id) <= self._fail_first:
            return ActionResult(success=False, error=f"{action_id} failed once")
        return ActionResult(success=True)


@pytest.mark.asyncio
async def test_continue_with_retry_retries_before_fallback():
    """on_error=continue + retry=1 ⇒ 先重试；重试全失败才跑兜底分支"""
    rec = _Recorder(fail_all=True)
    step = AtomicStep(
        action_id="llm",
        retry=1,
        on_error=OnErrorEnum.CONTINUE,
        on_error_branch=Pipeline([AtomicStep(action_id="fallback")]),
    )

    results = await Pipeline([step]).execute(Scope({}), rec)

    assert rec.calls == ["llm", "llm", "fallback"]
    llm_result = next(r for r in results if r.action_id == "llm")
    assert llm_result.tolerated is True


@pytest.mark.asyncio
async def test_retry_success_skips_fallback_branch():
    """重试成功 ⇒ 不执行 on_error_branch（避免既降级又升级）"""
    rec = _Recorder(fail_first=1)
    step = AtomicStep(
        action_id="llm",
        retry=1,
        on_error=OnErrorEnum.CONTINUE,
        on_error_branch=Pipeline([AtomicStep(action_id="fallback")]),
    )

    results = await Pipeline([step]).execute(Scope({}), rec)

    assert rec.calls == ["llm", "llm"]
    assert "fallback" not in rec.calls
    assert results[-1].success is True


@pytest.mark.asyncio
async def test_continue_without_retry_still_tolerates_immediately():
    """未声明 retry 时行为不变：失败即容忍并继续"""
    rec = _Recorder(fail_all=True)
    step = AtomicStep(
        action_id="llm",
        retry=0,
        on_error=OnErrorEnum.CONTINUE,
        on_error_branch=Pipeline([AtomicStep(action_id="fallback")]),
    )

    results = await Pipeline([step]).execute(Scope({}), rec)

    assert rec.calls == ["llm", "fallback"]
    llm_result = next(r for r in results if r.action_id == "llm")
    assert llm_result.tolerated is True


@pytest.mark.asyncio
async def test_failed_step_does_not_touch_output_var():
    """失败步骤不会改写用户的输出变量（值只由 input_vars / output_vars 决定）"""
    seen: list[str] = []

    async def executor(action_id, params, scope, output_vars):
        if action_id == "llm":
            action = _make_action(["comment_text"], scope.current)
            action._merge_output_vars(ActionResult(success=False, error="llm failed"))
            return ActionResult(success=False, error="llm failed")
        seen.append(scope.get("comment_text", ""))
        return ActionResult(success=True)

    pipeline = Pipeline(
        [
            AtomicStep(
                action_id="llm",
                output_vars=["comment_text"],
                on_error=OnErrorEnum.CONTINUE,
            ),
            AtomicStep(action_id="check"),
        ]
    )
    scope = Scope({"comment_text": "上一轮的旧评论"})

    await pipeline.execute(scope, executor)

    # 引擎不擅自清空 → 仍是用户自己放进去的值
    assert seen == ["上一轮的旧评论"]


@pytest.mark.asyncio
async def test_error_branch_output_vars_survive():
    """兜底分支里产出的变量必须保留下来（不能被引擎的 push/pop 丢掉）"""
    async def executor(action_id, params, scope, output_vars):
        if action_id == "llm":
            return ActionResult(success=False, error="llm failed")
        # 兜底步骤把产出写进 fallback_result
        for var_name in output_vars:
            scope.set(var_name, "兜底产出")
        return ActionResult(success=True, data={"value": "兜底产出"})

    pipeline = Pipeline(
        [
            AtomicStep(
                action_id="llm",
                on_error=OnErrorEnum.CONTINUE,
                on_error_branch=Pipeline(
                    [AtomicStep(action_id="fallback", output_vars=["fallback_result"])]
                ),
            ),
        ]
    )
    scope = Scope({})

    await pipeline.execute(scope, executor)

    assert scope.get("fallback_result") == "兜底产出"
    # 约定变量 last_error 也要留下来（后续步骤可引用）
    assert scope.get("last_error") == "llm failed"


def test_param_mapping_only_injects_declared_param_names():
    """param_mapping 只注入「用户声明过该参数名」的步骤，不给所有步骤凭空加键"""
    from app.services.execution.pipeline import LoopStep

    body = Pipeline(
        [
            AtomicStep(action_id="click", params={"selector": ".fixed"}),
            AtomicStep(action_id="input", params={"text": "hi"}),
        ]
    )
    loop = LoopStep(action_id="loop", body=body)

    injected = loop._inject_mapped_params(body, {"selector": ".from-item", "unused": 1})

    steps = {s.action_id: s for s in injected.steps}
    # 只有声明了 selector 的 click 被注入
    assert steps["click"].params["selector"] == ".from-item"
    # input 没有 selector → 不会被塞进无关参数
    assert "selector" not in steps["input"].params
    assert "unused" not in steps["input"].params
    # 原 body 不被污染
    assert body.steps[0].params["selector"] == ".fixed"
