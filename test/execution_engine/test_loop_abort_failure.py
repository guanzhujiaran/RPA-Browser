"""循环中断失败 → 运行直接判失败（不看容忍失败阈值/占比）的单元测试。

背景（2026-11-03）：循环体内步骤失败会把整层循环打断，剩余数据完全没跑；
但如果循环步骤本身配了 `on_error=continue`，这次失败会被标记 tolerated，
旧的「容忍失败阈值」逻辑在失败数未达阈值时仍判定 SUCCESS —— 用户看到
「全部成功」，实际只处理了前几条。

新口径：
1. `LoopStep` 检测到「体内存在未被容忍的失败」时，向进度上报器声明
   `aborted_by_failure=True`；
2. 上报器记录该标记（`loop_aborted_by_failure` / `LoopRunSummary`）；
3. `run_workflow` 汇总时一旦看到该标记，**直接**判 FAILED（不看阈值/占比）。
"""

from datetime import datetime

import pytest

from app.models.runtime.workflow_progress import (
    LoopRunSummary,
    WorkflowRunProgressData,
)
from app.services.execution.actions.base import ActionResult
from app.services.execution.pipeline import LoopStep, Pipeline
from app.services.execution.workflow.run_workflow import _build_loop_abort_message
from app.services.execution.workflow_progress import (
    WorkflowRunProgressReporter,
    workflow_run_reporters,
)


def make_reporter(run_id: str = "run-loop-abort-test") -> WorkflowRunProgressReporter:
    now = datetime.now()
    data = WorkflowRunProgressData(
        run_id=run_id,
        workflow_id=1,
        started_at=now,
        updated_at=now,
    )
    return WorkflowRunProgressReporter(data, persist_interval=999999)


class _FakeProgress:
    """只记录 loop_finished 的关键参数。"""

    def __init__(self) -> None:
        self.flags: list[bool] = []

    async def loop_finished(
        self, action_id: str, depth: int = 1, *, aborted_by_failure: bool = False
    ) -> None:
        self.flags.append(aborted_by_failure)


class _FakeScope:
    """LoopStep.execute 需要的最小作用域（不发消息、不解析变量）。"""

    def __init__(self, progress: _FakeProgress) -> None:
        self.loop_nesting = 0
        self.progress = progress

    def is_cancelled(self) -> bool:
        return False


class TestLoopStepReportsAbort:
    @pytest.mark.asyncio
    async def test_failure_results_mark_aborted(self):
        progress = _FakeProgress()
        loop = LoopStep(action_id="loop", body=Pipeline([]), count=3)

        async def fake_loop(*_args, **_kwargs):
            return [ActionResult(success=False, error="boom")], 1

        loop._loop_by_count = fake_loop  # type: ignore[method-assign]
        loop._report_iteration = _noop  # type: ignore[method-assign]  # noqa: SLF001

        await loop.execute(_FakeScope(progress), executor=None)  # type: ignore[arg-type]

        assert progress.flags == [True], "体内未容忍失败 → 循环被判定为失败中断"

    @pytest.mark.asyncio
    async def test_tolerated_results_not_marked(self):
        progress = _FakeProgress()
        loop = LoopStep(action_id="loop", body=Pipeline([]), count=3)

        async def fake_loop(*_args, **_kwargs):
            result = ActionResult(success=False, error="soft")
            result.tolerated = True
            return [result], 1

        loop._loop_by_count = fake_loop  # type: ignore[method-assign]
        loop._report_iteration = _noop  # type: ignore[method-assign]  # noqa: SLF001

        await loop.execute(_FakeScope(progress), executor=None)  # type: ignore[arg-type]

        assert progress.flags == [False], "on_error=continue 容忍的失败不算中断"


async def _noop(*_args, **_kwargs) -> None:
    return None


class TestReporterLoopAbort:
    def teardown_method(self):
        workflow_run_reporters.pop("run-loop-abort-test", None)

    @pytest.mark.asyncio
    async def test_flag_and_summary(self):
        reporter = make_reporter()
        # 先上报一轮，让上报器建好该层（真实执行里 loop_iteration 每轮都会发）
        await reporter.loop_iteration(
            action_id="loop", name="测试循环", index=1, total=5, depth=1
        )
        await reporter.loop_finished("loop", depth=1, aborted_by_failure=True)

        assert reporter.loop_aborted_by_failure is True
        assert reporter.loop_abort_count == 1
        assert reporter.loop_summaries[0].aborted_by_failure is True
        assert reporter.loop_summaries[0].rounds == 1

    @pytest.mark.asyncio
    async def test_normal_finish_keeps_success_semantics(self):
        reporter = make_reporter()
        await reporter.loop_iteration(
            action_id="loop", name="测试循环", index=1, total=5, depth=1
        )
        await reporter.loop_finished("loop", depth=1, aborted_by_failure=False)

        assert reporter.loop_aborted_by_failure is False
        assert reporter.loop_abort_count == 0
        assert reporter.loop_summaries[0].aborted_by_failure is False


class TestAbortMessage:
    def test_message_mentions_loop_and_rounds(self):
        summaries = [
            LoopRunSummary(
                name="第三方评论抽奖",
                depth=1,
                rounds=3,
                rounds_planned=20,
                body_failed=1,
                aborted_by_failure=True,
            )
        ]

        text = _build_loop_abort_message(loop_summaries=summaries, abort_count=1)

        assert "循环中断" in text
        assert "第三方评论抽奖" in text
        assert "3/20" in text
        assert "判定为失败" in text

    def test_message_without_summaries_is_generic(self):
        text = _build_loop_abort_message(loop_summaries=[], abort_count=1)

        assert "循环因步骤失败被中断" in text
