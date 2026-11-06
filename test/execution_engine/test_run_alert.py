"""运行告警（被容忍的失败）统计与通知文案单测（离线）

对应 ``docs/通知动作与运行告警计划书.md`` 的 A / D4 部分：
- `WorkflowRunProgressReporter` 统计 `on_error=continue` 容忍的失败；
- 循环聚合（`LoopRunSummary`）：轮次 + 体内动作成败，顶层步骤计数看不见该维度；
- `_run_notify_content` 在运行结束通知里渲染「运行告警」段与「循环统计」行；
- 容错熔断文案（`_build_tolerated_breaker_message`）带步骤级 + 循环级上下文；
- `WorkflowRunSummary` 新字段有默认值（历史事件可读）。
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models.runtime.workflow_progress import LoopRunSummary, render_loop_summaries
from app.models.runtime.workflow_progress import WorkflowRunProgressData
from app.services.execution.run_events import WorkflowRunSummary
from app.services.execution.workflow.notify import _run_notify_content
from app.services.execution.workflow.run_workflow import (
    _build_resource_failure_message,
    _build_tolerated_breaker_message,
    _collect_resource_stats,
    _resolve_run_counts,
    _should_trip_tolerated_breaker,
)
from app.models.execution.resource_stats import RunResourceStats
from app.services.execution.workflow_progress import (
    WorkflowRunProgressReporter,
    workflow_run_reporters,
)


def make_reporter(run_id: str = "run-alert-test") -> WorkflowRunProgressReporter:
    # started_at 必须传 naive datetime（与生产一致：取自 WorkflowRunRecord），
    # 传 int 会被 UnixTime 转成带时区 datetime，与 _refresh_duration 的
    # datetime.now()（naive）相减报 TypeError
    now = datetime.now()
    data = WorkflowRunProgressData(
        run_id=run_id,
        workflow_id=1,
        started_at=now,
        updated_at=now,
    )
    return WorkflowRunProgressReporter(data, persist_interval=999999)


class TestToleratedStats:
    def teardown_method(self):
        workflow_run_reporters.pop("run-alert-test", None)

    def test_counts_only_tolerated_failures(self):
        reporter = make_reporter()

        reporter._record_tolerated(
            SimpleNamespace(tolerated=True), "[LLM(llm)] Error code: 429"
        )
        # 未被容忍的失败不算告警（那是运行结论里的 failed）
        reporter._record_tolerated(SimpleNamespace(tolerated=False), "boom")
        reporter._record_tolerated(None, "boom")

        assert reporter.tolerated_count == 1
        assert reporter.tolerated_samples == ["[LLM(llm)] Error code: 429"]

    def test_samples_deduped_and_capped(self):
        reporter = make_reporter()

        for _ in range(5):
            reporter._record_tolerated(SimpleNamespace(tolerated=True), "same-err")
        assert reporter.tolerated_count == 5
        assert reporter.tolerated_samples == ["same-err"], "相同错误只保留一条样例"

        for i in range(4):
            reporter._record_tolerated(SimpleNamespace(tolerated=True), f"err-{i}")
        assert len(reporter.tolerated_samples) <= 3, "样例最多 3 条"

    def test_sample_truncated(self):
        reporter = make_reporter()
        reporter._record_tolerated(SimpleNamespace(tolerated=True), "x" * 500)

        assert len(reporter.tolerated_samples[0]) == 200

    def test_signatures_dedupe_same_error(self):
        """同一错误反复发生只算 1 类问题（熔断按「问题种类」而不是次数）。"""
        reporter = make_reporter()

        for _ in range(60):
            reporter._record_tolerated(
                SimpleNamespace(tolerated=True), "[LLM(llm)] 429 额度耗尽"
            )
        reporter._record_tolerated(SimpleNamespace(tolerated=True), "点击超时")

        assert reporter.tolerated_count == 61
        assert reporter.tolerated_signature_count == 2


class TestRunNotifyContent:
    def _workflow(self):
        return SimpleNamespace(name="测试工作流", workflow_id=106033248, trigger_type="manual")

    def test_alert_section_rendered(self):
        content = _run_notify_content(
            self._workflow(),
            browser_id=104660032,
            run_id="85e2bea6cc534e02",
            summary={
                "total": 18,
                "success": 18,
                "failed": 0,
                "tolerated": 91,
                "tolerated_samples": ["[LLM(llm)] Error code: 429 - free-models-per-day"],
            },
        )

        assert "运行告警：91 个步骤失败但已容错继续" in content
        assert "[LLM(llm)] Error code: 429" in content
        assert "不改变运行结论" in content

    def test_no_alert_when_none_tolerated(self):
        content = _run_notify_content(
            self._workflow(),
            browser_id=1,
            run_id="run-ok",
            summary={"total": 4, "success": 4, "failed": 0},
        )

        assert "运行告警" not in content

    def test_summary_defaults_backward_compatible(self):
        summary = WorkflowRunSummary(total=3, success_count=3)

        assert summary.tolerated_count == 0
        assert summary.tolerated_samples == []
        assert summary.loop_summaries == []
        assert summary.resource_total == 0
        assert summary.resource_failed == 0

    def test_resource_result_line(self):
        """资源级结果是业务工作流的主指标，通知必须能看到。"""
        content = _run_notify_content(
            self._workflow(),
            browser_id=1,
            run_id="run-res",
            summary={
                "total": 18,
                "success": 18,
                "failed": 0,
                "resource_total": 77,
                "resource_success": 62,
                "resource_failed": 13,
                "resource_skipped": 2,
            },
        )

        assert "资源结果：成功 62 / 失败 13 / 跳过 2（共 77）" in content

    def test_no_resource_line_without_resource_records(self):
        content = _run_notify_content(
            self._workflow(),
            browser_id=1,
            run_id="run-nores",
            summary={"total": 4, "success": 4, "failed": 0},
        )

        assert "资源结果" not in content

    def test_alert_section_with_loop_stats(self):
        content = _run_notify_content(
            self._workflow(),
            browser_id=1,
            run_id="run-loop",
            summary={
                "total": 1,
                "success": 1,
                "failed": 0,
                "tolerated": 91,
                "tolerated_samples": ["err"],
                "loop_summaries": [
                    LoopRunSummary(
                        name="批量点赞",
                        depth=1,
                        rounds=18,
                        rounds_planned=20,
                        body_success=54,
                        body_failed=37,
                    )
                ],
            },
        )

        assert (
            "循环统计：循环「批量点赞」执行 18/20 轮、体内 成功 54/失败 37" in content
        )

    def test_alert_section_without_loop_stats(self):
        content = _run_notify_content(
            self._workflow(),
            browser_id=1,
            run_id="run-noloop",
            summary={"total": 4, "success": 3, "failed": 1, "tolerated": 1},
        )

        assert "循环统计" not in content


class TestLoopSummaries:
    """循环聚合（LoopRunSummary）单测（见计划书 §D4）"""

    def setup_method(self):
        self.reporter = make_reporter("run-loop-test")
        # 进度落库是旁路关注点：测试里屏蔽，避免依赖 DB
        self.reporter._persist = AsyncMock()

    def teardown_method(self):
        workflow_run_reporters.pop("run-loop-test", None)

    async def test_single_loop_rounds_and_body_counts(self):
        r = self.reporter
        for i in range(1, 4):
            await r.loop_iteration(
                action_id="loop",
                name="批量点赞",
                index=i,
                total=3,
                depth=1,
                body_total=2,
            )
            await r.body_step_finished(0, 2, "点击", success=True, depth=1)
            await r.body_step_finished(
                1,
                2,
                "发送",
                success=i != 2,
                error=None if i != 2 else "boom",
                depth=1,
                result=SimpleNamespace(tolerated=i == 2, error="boom"),
            )
        await r.loop_finished(action_id="loop", depth=1)

        assert len(r.loop_summaries) == 1
        s = r.loop_summaries[0]
        assert s.name == "批量点赞"
        assert s.depth == 1
        assert s.rounds == 3
        assert s.rounds_planned == 3
        assert s.body_success == 5
        assert s.body_failed == 1

    async def test_early_exit_rounds_less_than_planned(self):
        r = self.reporter
        await r.loop_iteration(
            action_id="loop", name="遍历动态", index=1, total=35, depth=1
        )
        await r.body_step_finished(0, 1, "处理", success=True, depth=1)
        await r.loop_finished(action_id="loop", depth=1)

        s = r.loop_summaries[0]
        assert s.rounds == 1 and s.rounds_planned == 35

    async def test_nested_loops_each_layer(self):
        r = self.reporter
        for outer in (1, 2):
            await r.loop_iteration(
                action_id="outer",
                name="外循环",
                index=outer,
                total=2,
                depth=1,
                body_total=1,
            )
            await r.body_step_started(0, 1, "准备", depth=1)
            await r.body_step_finished(0, 1, "准备", success=True, depth=1)
            inner_total = 2 if outer == 1 else 1
            for inner in range(1, inner_total + 1):
                await r.loop_iteration(
                    action_id="inner", name="内循环", index=inner, total=2, depth=2
                )
                failed = outer == 1 and inner == 2
                await r.body_step_finished(
                    0,
                    1,
                    "点赞",
                    success=not failed,
                    error="e" if failed else None,
                    depth=2,
                )
            await r.loop_finished(action_id="inner", depth=2)
        await r.loop_finished(action_id="outer", depth=1)

        by_name = {s.name: s for s in r.loop_summaries}
        # 「准备」是外层体内动作，两轮都成功；内循环的动作不占外层计数
        assert by_name["外循环"].rounds == 2
        assert by_name["外循环"].body_success == 2
        assert by_name["外循环"].body_failed == 0
        # 内循环跑了两遍实例（外层每轮各一遍）：第一遍 2 轮 1 败，第二遍 1 轮
        inner = [s for s in r.loop_summaries if s.name == "内循环"]
        assert len(inner) == 2
        assert sum(s.rounds for s in inner) == 3
        assert sum(s.body_success for s in inner) == 2
        assert sum(s.body_failed for s in inner) == 1

    async def test_no_loop_no_summary(self):
        await self.reporter.step_started(0, "click", "点击")
        await self.reporter.step_finished(True)

        assert self.reporter.loop_summaries == []

    async def test_unfinished_loop_counts_discarded_on_new_step(self):
        r = self.reporter
        # 循环未结束就开新步骤（中断场景）：未固化计数被清掉，不留脏数据
        await r.loop_iteration(
            action_id="loop", name="批量点赞", index=1, total=3, depth=1
        )
        await r.body_step_finished(0, 1, "点击", success=True, depth=1)
        await r.step_started(1, "click", "点击")
        await r.loop_finished(action_id="loop", depth=1)

        assert r.loop_summaries == []


class TestRenderLoopSummaries:
    def test_empty(self):
        assert render_loop_summaries([]) == ""

    def test_single_instance_with_planned(self):
        s = LoopRunSummary(
            name="批量点赞",
            depth=1,
            rounds=18,
            rounds_planned=20,
            body_success=54,
            body_failed=6,
        )

        assert (
            render_loop_summaries([s])
            == "循环「批量点赞」执行 18/20 轮、体内 成功 54/失败 6"
        )

    def test_condition_loop_no_denominator(self):
        s = LoopRunSummary(name="遍历动态", rounds=18, body_success=18)

        assert (
            render_loop_summaries([s]) == "循环「遍历动态」执行 18 轮、体内 成功 18/失败 0"
        )

    def test_same_name_merged_without_fake_denominator(self):
        # 嵌套循环里内层每轮重跑：同名多次实例化合并为一条，不显示 N/M
        a = LoopRunSummary(
            name="内循环", depth=2, rounds=2, rounds_planned=2, body_success=1, body_failed=1
        )
        b = LoopRunSummary(name="内循环", depth=2, rounds=1, rounds_planned=2, body_success=1)

        assert (
            render_loop_summaries([a, b]) == "循环「内循环」执行 3 轮、体内 成功 2/失败 1"
        )

    def test_multiple_named_loops_joined(self):
        a = LoopRunSummary(name="循环A", rounds=2, rounds_planned=2, body_success=2)
        b = LoopRunSummary(name="循环B", rounds=1, rounds_planned=1, body_failed=1)

        assert (
            render_loop_summaries([a, b])
            == "循环「循环A」执行 2/2 轮、体内 成功 2/失败 0；"
            "循环「循环B」执行 1/1 轮、体内 成功 0/失败 1"
        )


class TestToleratedBreakerMessage:
    """容错熔断文案：步骤级 + 循环级全量上下文（见计划书 §D4）"""

    def test_with_step_and_loop_context(self):
        msg = _build_tolerated_breaker_message(
            success_count=1,
            total=1,
            tolerated_count=91,
            threshold=10,
            loop_summaries=[
                LoopRunSummary(
                    name="批量点赞",
                    depth=1,
                    rounds=18,
                    rounds_planned=20,
                    body_success=54,
                    body_failed=37,
                )
            ],
            samples=["[LLM(llm)] 429", "点击超时"],
        )

        assert msg.startswith("步骤成功 1/1，91 次失败被容错跳过（熔断阈值 10）")
        assert "循环「批量点赞」执行 18/20 轮、体内 成功 54/失败 37" in msg
        assert msg.endswith("判定为失败；示例: [LLM(llm)] 429；点击超时")

    def test_without_loops(self):
        msg = _build_tolerated_breaker_message(
            success_count=12,
            total=16,
            tolerated_count=8,
            threshold=10,
            loop_summaries=[],
            samples=[],
        )

        assert msg == "步骤成功 12/16，8 次失败被容错跳过（熔断阈值 10），判定为失败"


class TestResolveRunCounts:
    """运行步骤计数：进度上报器是唯一口径，不能与 results_data 叠加

    历史问题：`total=18` 的运行被写成 `success=36`（进度记 18 次 + 返回值又算 18 次），
    通知正文出现「成功 36 / 失败 0（共 18 步）」。
    """

    def test_progress_counts_are_not_double_counted(self):
        results = [{"success": True} for _ in range(18)]
        success, failed, fatal = _resolve_run_counts(
            results_data=results, progress_success=18, progress_failed=0
        )
        assert (success, failed, fatal) == (18, 0, 0)

    def test_tolerated_failure_is_counted_but_not_fatal(self):
        results = [
            {"success": True},
            {"success": False, "tolerated": True},
        ]
        success, failed, fatal = _resolve_run_counts(
            results_data=results, progress_success=1, progress_failed=1
        )
        assert (success, failed) == (1, 1), "被容忍的失败也要计入失败数（便于展示）"
        assert fatal == 0, "被容忍的失败不决定运行结论"

    def test_fatal_failure_decides_run_status(self):
        results = [
            {"success": False, "tolerated": False},
            {"success": False},
        ]
        _, _, fatal = _resolve_run_counts(
            results_data=results, progress_success=0, progress_failed=2
        )
        assert fatal == 2

    def test_resume_inherits_previous_counts(self):
        """断点恢复：进度计数已含继承值，结果只含本次执行的步骤。"""
        results = [{"success": True} for _ in range(13)]
        success, failed, fatal = _resolve_run_counts(
            results_data=results, progress_success=18, progress_failed=0
        )
        assert (success, failed, fatal) == (18, 0, 0)


class TestResourceFailureJudgement:
    """资源失败是运行结论的主判据（步骤成功 ≠ 业务办成）"""

    def test_message_contains_all_three_buckets(self):
        stats = RunResourceStats(total=77, success=62, failed=13, skipped=2)
        msg = _build_resource_failure_message(stats, threshold=1)

        assert "成功 62 / 失败 13 / 跳过 2（共 77）" in msg
        assert "13 条资源处理失败（阈值 1）" in msg
        assert "下一轮会被重新捞起" in msg

    def test_stats_helpers(self):
        empty = RunResourceStats()
        assert not empty.has_data
        assert empty.failed_ratio == 0.0

        stats = RunResourceStats(total=4, success=2, failed=1, skipped=1)
        assert stats.has_data
        assert stats.failed_ratio == 0.25

    async def test_collect_falls_back_to_empty_on_error(self, monkeypatch):
        """统计查询失败不能拖垮运行收口：退化为无资源数据。"""

        async def _boom(_run_id: str):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "app.services.execution.crud_service.resource_record_crud."
            "ResourceRecordService.count_by_execution",
            _boom,
        )
        stats = await _collect_resource_stats("run-x")
        assert not stats.has_data


class TestToleratedBreakerDecision:
    """熔断判定：按「不同错误类型 + 循环体内失败占比」，不再只看绝对次数

    历史问题（2026-11-06 run 4e7b37d）：75 条数据里 62 条成功，
    仅因 LLM 额度耗尽在每条数据上降级一次（共 60 次 + 空列表 2 次）
    就触发阈值 10，整轮被标成 FAILED。
    """

    def _summaries(self, success: int, failed: int):
        return [
            LoopRunSummary(
                name="批量参与",
                depth=1,
                rounds=1,
                rounds_planned=1,
                body_success=success,
                body_failed=failed,
            )
        ]

    def test_below_threshold_never_trips(self):
        tripped, _ = _should_trip_tolerated_breaker(
            tolerated_count=9,
            threshold=10,
            signature_count=9,
            loop_summaries=self._summaries(100, 9),
            max_ratio=0.3,
        )
        assert not tripped

    def test_same_error_repeated_low_ratio_does_not_trip(self):
        """同一降级点重复 62 次、占比 1.3% → 不判失败（本轮真实数据）。"""
        tripped, ratio = _should_trip_tolerated_breaker(
            tolerated_count=62,
            threshold=10,
            signature_count=5,
            loop_summaries=self._summaries(4691, 62),
            max_ratio=0.3,
        )
        assert not tripped
        assert 0 < ratio < 0.3

    def test_high_failure_ratio_trips_even_with_one_signature(self):
        """「几乎全失败」仍然要拦：1 类错误 + 100% 失败占比。"""
        tripped, ratio = _should_trip_tolerated_breaker(
            tolerated_count=100,
            threshold=10,
            signature_count=1,
            loop_summaries=self._summaries(0, 100),
            max_ratio=0.3,
        )
        assert tripped
        assert ratio == 1.0

    def test_many_distinct_errors_trip(self):
        """低占比但多种能力同时坏了（不同错误数达阈值）→ 判失败。"""
        tripped, _ = _should_trip_tolerated_breaker(
            tolerated_count=12,
            threshold=10,
            signature_count=12,
            loop_summaries=self._summaries(1000, 12),
            max_ratio=0.3,
        )
        assert tripped

    def test_no_loop_keeps_old_semantics(self):
        tripped, ratio = _should_trip_tolerated_breaker(
            tolerated_count=10,
            threshold=10,
            signature_count=1,
            loop_summaries=[],
            max_ratio=0.3,
        )
        assert tripped
        assert ratio == 1.0

    def test_ratio_zero_falls_back_to_absolute_threshold(self):
        tripped, _ = _should_trip_tolerated_breaker(
            tolerated_count=10,
            threshold=10,
            signature_count=1,
            loop_summaries=self._summaries(1000, 10),
            max_ratio=0.0,
        )
        assert tripped

    def test_threshold_zero_disables_breaker(self):
        tripped, _ = _should_trip_tolerated_breaker(
            tolerated_count=999,
            threshold=0,
            signature_count=99,
            loop_summaries=self._summaries(0, 999),
            max_ratio=0.3,
        )
        assert not tripped
