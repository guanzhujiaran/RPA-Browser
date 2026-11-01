"""LLM 动作失败的集成测试（真实 LLMAction + 真实 pipeline，无浏览器）

用真实的 ``LLMAction`` 去连一个**必定连接失败**的本地端点（127.0.0.1:9），
在真实链路上验证三件事：

1. **``retry=0`` ⇒ 只调用一次**：不重试、不多打一次请求（避免 429 额度
   耗尽时白白消耗额度）；
2. **失败后执行 ``on_error_branch`` 兜底分支**，且兜底分支里能读到
   ``last_error``；
3. **引擎不擅自改写用户变量**：LLM 失败（无产出）后 ``comment_text``
   保持原值——变量该是什么值由用户的 ``input_vars`` / ``output_vars`` 决定。

不涉及浏览器：LLM 动作与 print 动作都不需要页面。
"""

from __future__ import annotations

import pytest

from app.models.execution.action_params import OnErrorEnum
from app.services.execution.actions.base import ActionResult
from app.services.execution.actions.llm import LLMAction
from app.services.execution.pipeline import AtomicStep, Pipeline
from app.services.execution.scope import Scope

#: 必定连接失败的端点（discard 端口），用于稳定触发 LLM 调用失败
_DEAD_ENDPOINT = "http://127.0.0.1:9/v1"

_LLM_PARAMS = {
    "model": "test-model",
    "prompt": "ping",
    "api_key": "sk-test",
    "server_url": _DEAD_ENDPOINT,
    "timeout": 3000,
    "system_prompt": "s",
}


def _llm_executor(calls: list[str]):
    """按 engine._run_action_core 的核心逻辑构造 executor（跳过浏览器与日志）"""

    async def _exec(action_id, params, scope, output_vars) -> ActionResult:
        calls.append(action_id)
        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables=scope.current,
            params=params,
            output_vars=list(output_vars),
        )
        return await action.execute()

    return _exec


@pytest.mark.asyncio
async def test_llm_failure_calls_once_and_runs_fallback_branch():
    """retry=0 ⇒ LLM 只调用一次，随后执行兜底分支"""
    calls: list[str] = []

    async def executor(action_id, params, scope, output_vars):
        calls.append(action_id)
        if action_id == "llm":
            action = LLMAction.new_action(
                mid=1,
                page=None,
                variables=scope.current,
                params=params,
                output_vars=list(output_vars),
            )
            return await action.execute()
        # 兜底步骤：把结果写回变量，便于断言兜底确实执行过
        for var_name in output_vars:
            scope.set(var_name, "兜底产出")
        return ActionResult(success=True, data={"value": "兜底产出"})

    pipeline = Pipeline(
        [
            AtomicStep(
                action_id="llm",
                retry=0,
                on_error=OnErrorEnum.CONTINUE,
                params=dict(_LLM_PARAMS),
                output_vars=["comment_text"],
                on_error_branch=Pipeline(
                    [
                        AtomicStep(
                            action_id="fallback",
                            output_vars=["fallback_ran"],
                        )
                    ]
                ),
            )
        ]
    )
    scope = Scope({"comment_text": "用户自己设置的值"})

    results = await pipeline.execute(scope, executor)

    # ① 只调用一次（retry=0）
    assert calls.count("llm") == 1
    # ② 兜底分支执行了，且 last_error 在兜底分支内可见
    assert "fallback" in calls
    assert scope.get("fallback_ran") == "兜底产出"
    assert scope.get("last_error")
    # ③ 引擎没有擅自清空用户的变量
    assert scope.get("comment_text") == "用户自己设置的值"


@pytest.mark.asyncio
async def test_llm_failure_result_has_no_data():
    """LLM 失败 ⇒ data 为 None（没有产出），变量不被写入"""
    variables = {"comment_text": "原值"}
    action = LLMAction.new_action(
        mid=1,
        page=None,
        variables=variables,
        params=dict(_LLM_PARAMS),
        output_vars=["comment_text"],
    )

    result = await action.execute()

    assert result.success is False
    assert result.data is None
    assert variables["comment_text"] == "原值"
