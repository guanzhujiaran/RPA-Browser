"""循环名称（loop name）与运行消息参数明细测试

背景：批量工作流里循环没有名称，运行消息 / 进度条只能显示 `循环「loop」`；
基础操作的执行参数也不可见，轮次失败时无法从消息栏判断参数问题。
见 `docs/循环名称与运行消息明细计划书.md`。

覆盖：
    1. `LoopParams.name` 可选字段 + 步骤 JSON 编译（含向后兼容）；
    2. 循环名称逐轮按作用域解析（{{loop_item.xxx}} 模板），未设置回落 action_id；
    3. 运行消息 detail 展示执行参数（剔除嵌套子步骤字段 + 敏感字段脱敏）。
"""

import json
from pathlib import Path

import pytest

from app.models.execution.action_params import (
    LoopParams,
    OnErrorEnum,
    _ensure_action_type,
    workflow_step_adapter,
)
from app.services.execution.actions.base import ActionResult
from app.services.execution.pipeline import (
    AtomicStep,
    IfElseStep,
    LoopStep,
    Pipeline,
    PipelineBuilder,
)
from app.services.execution.scope import Scope
from app.services.execution.workflow_progress import build_step_detail

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTION_JSON = PROJECT_ROOT / "actions" / "action_104682832_参与B站抽奖.json"


class _RecordingReporter:
    """记录进度上报调用的假 reporter（只关心循环名称相关入参）"""

    def __init__(self) -> None:
        self.iterations: list[dict] = []
        self.finished: list[tuple[str, int]] = []
        self.steps_started: list[tuple[int, str, str]] = []
        self.tolerated: list[tuple[object, str | None]] = []
        self.steps_finished: list[tuple[bool, str | None]] = []
        self.body_finished: list[tuple[str, bool, str | None, int]] = []

    async def step_started(self, index: int, action_id: str, name: str = "") -> None:
        self.steps_started.append((index, action_id, name))

    async def step_finished(self, success, error=None, *, result=None) -> None:
        self.steps_finished.append((success, error))

    async def loop_iteration(
        self, *, action_id, name, index, total, depth, body_total=0
    ) -> None:
        self.iterations.append(
            {
                "action_id": action_id,
                "name": name,
                "index": index,
                "total": total,
                "depth": depth,
            }
        )

    async def loop_finished(self, action_id: str, depth: int = 1) -> None:
        self.finished.append((action_id, depth))

    def record_tolerated(self, result, error=None) -> None:
        """与真实 reporter 同名：LoopStep 会把循环级容忍的失败补登进来"""
        self.tolerated.append((result, error))

    async def body_step_started(self, index, total, step_name="", *, depth=1) -> None:
        pass

    async def body_step_finished(
        self,
        index,
        total,
        step_name="",
        success=True,
        error=None,
        *,
        result=None,
        duration_ms=None,
        depth=1,
    ) -> None:
        self.body_finished.append((step_name, success, error, depth))

    async def loop_body_step(self, completed, total, step_name="", depth=1) -> None:
        pass


async def _run_loop(loop_step: LoopStep, variables: dict) -> _RecordingReporter:
    """执行单个循环步骤，返回记录了上报调用的 reporter"""

    async def executor(action_id, params, scope, output_vars):
        return ActionResult(success=True, action_id=action_id)

    reporter = _RecordingReporter()
    scope = Scope(variables)
    scope.progress = reporter
    await Pipeline(steps=[loop_step]).execute(scope, executor, top_level=True)
    return reporter


def _items_loop(name: str) -> LoopStep:
    """遍历 dyn_list 的循环（循环体一个 print 步骤）"""
    return LoopStep(
        action_id="loop",
        name=name,
        loop_items_var="dyn_list",
        body=Pipeline(
            steps=[AtomicStep(action_id="print", params={"message": "hi"})]
        ),
    )


class TestLoopNameResolution:
    """循环名称：静态 / 模板 / 未设置"""

    @pytest.mark.asyncio
    async def test_static_name(self):
        """静态名称：每轮都展示同一个名称"""
        reporter = await _run_loop(
            _items_loop("遍历动态列表"),
            {"dyn_list": [{"title": "A"}, {"title": "B"}]},
        )
        assert [it["name"] for it in reporter.iterations] == [
            "遍历动态列表",
            "遍历动态列表",
        ]

    @pytest.mark.asyncio
    async def test_template_name_resolved_per_round(self):
        """模板名称：每轮按当前循环项解析，一眼看出本轮在处理哪条数据"""
        reporter = await _run_loop(
            _items_loop("处理 {{loop_item.title}}"),
            {"dyn_list": [{"title": "第一条"}, {"title": "第二条"}]},
        )
        assert [it["name"] for it in reporter.iterations] == [
            "处理 第一条",
            "处理 第二条",
        ]

    @pytest.mark.asyncio
    async def test_index_template_name(self):
        """模板里可用 loop_index"""
        reporter = await _run_loop(_items_loop("第 {{loop_index}} 条"), {"dyn_list": ["x", "y"]})
        assert [it["name"] for it in reporter.iterations] == ["第 0 条", "第 1 条"]

    @pytest.mark.asyncio
    async def test_missing_variable_keeps_literal_then_falls_back(self):
        """变量不存在：非严格解析保留字面量 → 回落 action_id，不把 {{xxx}} 当名称"""
        reporter = await _run_loop(
            _items_loop("处理 {{loop_item.title}}"),
            {"dyn_list": [{"other": 1}, {"other": 2}]},
        )
        assert [it["name"] for it in reporter.iterations] == ["loop", "loop"]

    @pytest.mark.asyncio
    async def test_no_name_falls_back_to_action_id(self):
        """未设置名称：行为与历史一致（loop）"""
        reporter = await _run_loop(_items_loop(""), {"dyn_list": ["x"]})
        assert [it["name"] for it in reporter.iterations] == ["loop"]

    @pytest.mark.asyncio
    async def test_step_display_name_uses_loop_name(self):
        """顶层步骤开始消息用解析后的循环名称，而不是裸 loop"""
        reporter = await _run_loop(
            _items_loop("遍历动态列表"), {"dyn_list": [{"title": "A"}]}
        )
        assert reporter.steps_started == [(0, "loop", "遍历动态列表")]


class TestLoopNameCompile:
    """编译与参数模型"""

    def test_params_model_accepts_name(self):
        params = LoopParams(name="处理 {{loop_item.title}}")
        assert params.name == "处理 {{loop_item.title}}"

    def test_params_model_name_optional(self):
        """不传 name：默认 None（存量动作 JSON 零改动）"""
        assert LoopParams().name is None

    def test_builder_reads_name_from_params(self):
        step = {
            "action_id": "loop",
            "action_type": "loop",
            "params": {
                "name": "遍历动态列表",
                "loop_source": "variable",
                "loop_items_var": "dyn_list",
            },
        }
        pipeline = PipelineBuilder.build(
            [workflow_step_adapter.validate_python(_ensure_action_type(step))]
        )
        loop_step = pipeline.steps[0]
        assert isinstance(loop_step, LoopStep)
        assert loop_step.name == "遍历动态列表"

    def test_builder_without_name_defaults_empty(self):
        """向后兼容：没有 name 的 loop 步骤编译为 name=''"""
        step = {
            "action_id": "loop",
            "action_type": "loop",
            "params": {"loop_source": "fixed_count", "count": 2},
        }
        pipeline = PipelineBuilder.build(
            [workflow_step_adapter.validate_python(_ensure_action_type(step))]
        )
        assert pipeline.steps[0].name == ""

    def test_loop_defaults_to_stop(self):
        """loop 未显式声明 on_error 时默认 stop（与 atomic / if_else 一致）

        历史问题：builder 里硬编码兜底 continue，且这个默认值不体现在动作 JSON 里，
        作者看不见 → 循环一路「成功」、实际什么都没做、还没有 error。
        批量容错必须显式写 `"on_error": "continue"`。
        """
        step = {
            "action_id": "loop",
            "action_type": "loop",
            "params": {"loop_source": "variable", "loop_items_var": "items"},
        }
        pipeline = PipelineBuilder.build(
            [workflow_step_adapter.validate_python(_ensure_action_type(step))]
        )
        assert pipeline.steps[0].on_error == OnErrorEnum.STOP

    def test_loop_explicit_continue_kept(self):
        """显式 on_error=continue 必须保留（批量容错靠它表态）"""
        step = {
            "action_id": "loop",
            "action_type": "loop",
            "on_error": "continue",
            "params": {"loop_source": "variable", "loop_items_var": "items"},
        }
        pipeline = PipelineBuilder.build(
            [workflow_step_adapter.validate_python(_ensure_action_type(step))]
        )
        assert pipeline.steps[0].on_error == OnErrorEnum.CONTINUE

    def test_lottery_action_json_still_compiles(self):
        """存量抽奖动作 JSON 不传 name，静态校验照旧通过"""
        raw = json.loads(ACTION_JSON.read_text(encoding="utf-8"))
        steps = raw.get("steps") or raw.get("data", {}).get("steps") or []
        if not steps:
            pytest.skip("抽奖动作 JSON 中未找到 steps")
        nodes = PipelineBuilder.build(
            [
                workflow_step_adapter.validate_python(_ensure_action_type(s))
                for s in steps
            ]
        )
        assert nodes.steps


class TestStepDetailParams:
    """运行消息 detail：执行参数展示"""

    def test_params_line_first(self):
        """detail 首行为执行参数（模板替换后的实际入参），其后是返回数据"""
        result = ActionResult(
            success=True,
            data={"ok": True},
            replaced_params={"selector": ".reply-item", "timeout": 3000},
        )
        detail = build_step_detail(result)
        assert detail is not None
        lines = detail.split("\n")
        assert lines[0].startswith("参数: ")
        assert ".reply-item" in lines[0]
        assert '"ok"' in lines[1]

    def test_nested_step_keys_stripped(self):
        """嵌套子步骤字段（steps / loopBranch / TrueBranch / FalseBranch）不铺开"""
        result = ActionResult(
            success=True,
            replaced_params={
                "selector": ".x",
                "steps": [{"action_id": "print"}],
                "loopBranch": [{"action_id": "click"}],
                "TrueBranch": [{"action_id": "wait"}],
                "FalseBranch": [{"action_id": "get_text"}],
            },
        )
        detail = build_step_detail(result)
        assert detail is not None
        assert "loopBranch" not in detail
        assert "TrueBranch" not in detail
        assert '"steps"' not in detail
        assert ".x" in detail

    def test_sensitive_keys_redacted(self):
        """敏感字段脱敏后再进 SSE（与操作日志同一套黑名单）"""
        result = ActionResult(
            success=True,
            replaced_params={"llm_api_key": "sk-should-not-leak", "prompt": "hi"},
        )
        detail = build_step_detail(result)
        assert detail is not None
        assert "sk-should-not-leak" not in detail
        assert "***" in detail
        assert "hi" in detail

    def test_no_params_keeps_legacy_detail(self):
        """无 replaced_params（如 loop 自身）：detail 只有返回数据 / 日志，无参数行"""
        result = ActionResult(success=True, data={"iterations": 3}, logs=["一条日志"])
        detail = build_step_detail(result)
        assert detail is not None
        assert "参数:" not in detail
        assert "一条日志" in detail

    def test_empty_result_returns_none(self):
        assert build_step_detail(ActionResult(success=True)) is None
        assert build_step_detail(None) is None


class TestLoopFailureVisibility:
    """循环内失败必须可见：`on_error=continue` 是容忍，不是静默。

    背景：循环级 continue 会把失败迭代标成 tolerated，此前该结果在 `LoopStep.execute`
    出口被整体丢弃，循环只报 `success=True / error=None`，用户只看到「循环成功但
    什么都没做」（不评论、不点赞、不转发），完全查不到原因。
    """

    @staticmethod
    def _loop(on_error: OnErrorEnum, items_var: str = "dyn_list") -> LoopStep:
        return LoopStep(
            action_id="loop",
            loop_items_var=items_var,
            on_error=on_error,
            body=Pipeline(
                steps=[AtomicStep(action_id="click", params={"selector": ".x"})]
            ),
        )

    @staticmethod
    def _failing_executor(message: str = "元素不存在"):
        async def executor(action_id, params, scope, output_vars):
            return ActionResult(success=False, error=message, action_id=action_id)

        return executor

    @pytest.mark.asyncio
    async def test_tolerated_failures_are_counted_and_sampled(self):
        """continue：结论仍是成功，但失败轮次/首个错误/失败动作必须带出来"""
        reporter = _RecordingReporter()
        scope = Scope({"dyn_list": ["a", "b"]})
        scope.progress = reporter

        results = await Pipeline(steps=[self._loop(OnErrorEnum.CONTINUE)]).execute(
            scope, self._failing_executor(), top_level=True
        )
        result = results[0]

        assert result.success is True, "on_error=continue 不应改变循环结论"
        assert result.data["iterations"] == 2
        assert result.data["failed_steps"] == 2
        assert result.data["first_error"] == "元素不存在"
        assert result.data["first_failed_action"] == "click"
        # 结论成功也要带可读的失败摘要：前端「错误」列 / 运行记录 error 字段靠它
        assert result.error and "on_error=continue" in result.error
        assert "元素不存在" in result.error
        # 结束通知的「容错样本」靠这里补登，否则永远为空
        assert len(reporter.tolerated) == 2

    @pytest.mark.asyncio
    async def test_stop_policy_reports_error_as_before(self):
        """默认 stop：结论失败，错误原文透出"""
        reporter = _RecordingReporter()
        scope = Scope({"dyn_list": ["a"]})
        scope.progress = reporter

        results = await Pipeline(steps=[self._loop(OnErrorEnum.STOP)]).execute(
            scope, self._failing_executor(), top_level=True
        )
        result = results[0]

        assert result.success is False
        assert result.error == "元素不存在"
        assert result.data["failed_steps"] == 0

    @pytest.mark.asyncio
    async def test_unresolvable_items_var_warns(self):
        """数据源解析不出列表 → 0 轮静默成功是「什么都没执行」最隐蔽的来源，必须留痕"""
        scope = Scope({})
        results = await Pipeline(
            steps=[self._loop(OnErrorEnum.CONTINUE, items_var="missing_list")]
        ).execute(scope, self._failing_executor(), top_level=True)
        result = results[0]

        assert result.data["iterations"] == 0
        assert "missing_list" in result.data["warning"]


class TestStepExceptionErrorText:
    """步骤抛异常时的错误文案：必须是被抛出的真实原因，不能是「未知错误」"""

    @pytest.mark.asyncio
    async def test_exception_error_is_reported(self):
        async def executor(action_id, params, scope, output_vars):
            raise RuntimeError("boom-真实原因")

        reporter = _RecordingReporter()
        scope = Scope({})
        scope.progress = reporter

        results = await Pipeline(
            steps=[AtomicStep(action_id="click", params={})]
        ).execute(scope, executor, top_level=True)

        assert results[0].success is False
        assert results[0].error == "boom-真实原因"
        assert reporter.steps_finished == [(False, "boom-真实原因")]


class TestNestedBranchReporting:
    """分支体内的动作必须出现在运行日志里。

    背景：`IfElseStep` 以前用默认参数执行分支体（`body_of_loop=False`），导致
    「循环 → if_else → 分支内的点击/输入/发送」这些真正干活的动作一条消息都不发，
    失败时只能看到 if_else 成功/失败，看不到是哪个动作、什么原因。
    """

    @pytest.mark.asyncio
    async def test_branch_step_failure_is_reported(self):
        async def executor(action_id, params, scope, output_vars):
            return ActionResult(success=False, error="输入框不可见", action_id=action_id)

        reporter = _RecordingReporter()
        scope = Scope({"dyn_list": ["a"]})
        scope.progress = reporter

        loop = LoopStep(
            action_id="loop",
            loop_items_var="dyn_list",
            body=Pipeline(
                steps=[
                    IfElseStep(
                        action_id="if_else",
                        expression="1 == 1",
                        true_body=Pipeline(
                            steps=[AtomicStep(action_id="input", params={})]
                        ),
                        false_body=None,
                    )
                ]
            ),
        )
        await Pipeline(steps=[loop]).execute(scope, executor, top_level=True)

        names = [name for name, *_ in reporter.body_finished]
        assert "input" in names, f"分支内动作未上报: {reporter.body_finished}"
        nested = [r for r in reporter.body_finished if r[0] == "input"][0]
        assert nested[1] is False
        assert nested[2] == "输入框不可见"
        # 嵌套分支上报层级比循环体更深（只发消息，不抢第二条进度条）
        assert nested[3] > 1

    @pytest.mark.asyncio
    async def test_toplevel_branch_step_is_reported(self):
        """顶层 if_else 的分支体也要上报（不带轮次、不计入 completed_steps）"""

        async def executor(action_id, params, scope, output_vars):
            return ActionResult(success=True, action_id=action_id)

        reporter = _RecordingReporter()
        scope = Scope({})
        scope.progress = reporter

        if_step = IfElseStep(
            action_id="if_else",
            expression="1 == 1",
            true_body=Pipeline(steps=[AtomicStep(action_id="get_text", params={})]),
            false_body=None,
        )
        await Pipeline(steps=[if_step]).execute(scope, executor, top_level=True)

        depth = [d for name, _, _, d in reporter.body_finished if name == "get_text"]
        assert depth == [1], f"顶层分支动作未上报或层级不对: {reporter.body_finished}"
        # 分支体是 if_else 的一部分，不能重复计入顶层完成数
        assert reporter.steps_finished == [(True, None)]
