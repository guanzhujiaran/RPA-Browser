"""0 步中断自动重跑（docs/同浏览器工作流排队计划书.md §4.1）

覆盖：候选筛选、幂等留痕、独立开关、整轮重跑（不传 resume_from）、
触发来源原样带入、工作流禁用 / 同浏览器并发防护、单条异常不阻塞启动。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.config import settings
from app.models.database.workflow.models import (
    WorkflowRunRecord,
    WorkflowRunStatusEnum,
    WorkflowRunTriggerEnum,
)
from app.models.database.workflow.resource_record import (
    ResourceProcessStatus,
    WorkflowResourceRecord,
)
from app.services.execution.crud_service.workflow_run_crud import (
    workflow_run_crud_svr,
)
from app.services.execution.workflow import launcher
from app.utils.depends.session_manager import DatabaseSessionManager

MID = "auto-restart-test-mid"
WORKFLOW_ID = 998877110099
BROWSER_ID = 1
MAX_AGE = 21600


async def _cleanup() -> None:
    async with DatabaseSessionManager.async_session() as session:
        for model, column in (
            (WorkflowRunRecord, WorkflowRunRecord.mid),
            (WorkflowResourceRecord, WorkflowResourceRecord.mid),
        ):
            rows = (
                await session.exec(  # type: ignore[attr-defined]
                    select(model).where(column == MID)  # type: ignore[arg-type]
                )
            ).all()
            for row in rows:
                await session.delete(row)
        await session.commit()


async def _insert_run(
    run_id: str,
    *,
    status: WorkflowRunStatusEnum = WorkflowRunStatusEnum.INTERRUPTED,
    checkpoint: int = 0,
    completed: int = 0,
    resumable: bool = False,
    finished_at: datetime | None = None,
    error_message: str | None = "服务重启，运行被中断",
    trigger: WorkflowRunTriggerEnum = WorkflowRunTriggerEnum.MANUAL,
) -> None:
    async with DatabaseSessionManager.async_session() as session:
        session.add(
            WorkflowRunRecord(
                run_id=run_id,
                workflow_id=WORKFLOW_ID,
                mid=MID,
                browser_id=BROWSER_ID,
                status=status,
                trigger_source=trigger,
                checkpoint_step=checkpoint,
                completed_steps=completed,
                resumable=resumable,
                finished_at=finished_at or datetime.now(),
                error_message=error_message,
                total=18,
            )
        )
        await session.commit()


async def _insert_resource(run_id: str) -> None:
    async with DatabaseSessionManager.async_session() as session:
        session.add(
            WorkflowResourceRecord(
                mid=MID,
                workflow_id=WORKFLOW_ID,
                browser_id=BROWSER_ID,
                execution_id=run_id,
                resource_type="auto_restart_test",
                resource_id="r1",
                status=ResourceProcessStatus.SUCCESS,
            )
        )
        await session.commit()


async def _read_run(run_id: str) -> WorkflowRunRecord:
    async with DatabaseSessionManager.async_session() as session:
        row = (
            await session.exec(  # type: ignore[attr-defined]
                select(WorkflowRunRecord).where(WorkflowRunRecord.run_id == run_id)
            )
        ).first()
    assert row is not None
    return row


@pytest.fixture(autouse=True)
async def _clean():
    await _cleanup()
    yield
    await _cleanup()


async def test_list_unstarted_interrupted_filters_candidates() -> None:
    """只有「0 步 + 无资源记录 + 未标记 + 近期」的 INTERRUPTED 入选。"""
    await _insert_run("ok")
    await _insert_run("has-checkpoint", checkpoint=3, completed=3, resumable=True)
    await _insert_run("has-resource")
    await _insert_resource("has-resource")
    await _insert_run(
        "already-marked",
        error_message="服务重启，运行被中断；已自动重跑 → abc",
    )
    await _insert_run(
        "too-old", finished_at=datetime.now() - timedelta(seconds=MAX_AGE + 60)
    )
    await _insert_run("failed", status=WorkflowRunStatusEnum.FAILED)
    await _insert_run("cancelled", status=WorkflowRunStatusEnum.CANCELLED)
    await _insert_run("success", status=WorkflowRunStatusEnum.SUCCESS)

    rows = await workflow_run_crud_svr.list_unstarted_interrupted(
        max_age_seconds=MAX_AGE
    )

    assert [r.run_id for r in rows] == ["ok"]


async def test_mark_auto_restarted_is_idempotent_and_keeps_terminal_status() -> None:
    await _insert_run("mark-me")

    assert await workflow_run_crud_svr.mark_auto_restarted("mark-me", "new-1")
    assert not await workflow_run_crud_svr.mark_auto_restarted("mark-me", "new-2")

    row = await _read_run("mark-me")
    assert row.status == WorkflowRunStatusEnum.INTERRUPTED, "终态不能被改写"
    assert "服务重启，运行被中断" in (row.error_message or "")
    assert "已自动重跑 → new-1" in (row.error_message or "")
    assert "new-2" not in (row.error_message or "")

    rows = await workflow_run_crud_svr.list_unstarted_interrupted(
        max_age_seconds=MAX_AGE
    )
    assert "mark-me" not in [r.run_id for r in rows]


def _fake_run(run_id: str = "old-run") -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        workflow_id=WORKFLOW_ID,
        mid=MID,
        browser_id=BROWSER_ID,
        status=WorkflowRunStatusEnum.INTERRUPTED,
        trigger_source=WorkflowRunTriggerEnum.SCHEDULE,
        resumable=False,
        checkpoint_step=0,
        completed_steps=0,
    )


def _mock_common(monkeypatch, *, enabled: bool = True, running: str | None = None):
    monkeypatch.setattr(settings, "workflow_auto_restart_unstarted_interrupted", True)
    monkeypatch.setattr(
        launcher.workflow_run_crud_svr,
        "list_unstarted_interrupted",
        AsyncMock(return_value=[_fake_run()]),
    )
    monkeypatch.setattr(
        launcher.workflow_crud_svr,
        "get_by_workflow_id",
        AsyncMock(
            return_value=SimpleNamespace(
                workflow_id=WORKFLOW_ID,
                browser_id=BROWSER_ID,
                mid=MID,
                is_enabled=enabled,
            )
        ),
    )
    monkeypatch.setattr(
        launcher.workflow_run_crud_svr,
        "get_running_for_browser",
        AsyncMock(return_value=running),
    )
    launch = AsyncMock(return_value="new-run")
    monkeypatch.setattr(launcher, "launch_workflow_run", launch)
    mark = AsyncMock(return_value=True)
    monkeypatch.setattr(
        launcher.workflow_run_crud_svr, "mark_auto_restarted", mark
    )
    return launch, mark


async def test_disabled_switch_skips_scan(monkeypatch) -> None:
    scan = AsyncMock(return_value=[])
    monkeypatch.setattr(settings, "workflow_auto_restart_unstarted_interrupted", False)
    monkeypatch.setattr(
        launcher.workflow_run_crud_svr, "list_unstarted_interrupted", scan
    )

    assert await launcher.relaunch_unstarted_interrupted_runs() == []
    scan.assert_not_awaited()


async def test_relaunch_starts_full_run_and_marks_old(monkeypatch) -> None:
    """整轮重跑：不带 resume_from；trigger_source 原样带入（SCHEDULE 也覆盖）。"""
    launch, mark = _mock_common(monkeypatch)

    assert await launcher.relaunch_unstarted_interrupted_runs() == ["new-run"]

    kwargs = launch.await_args.kwargs
    assert "resume_from" not in kwargs, "整轮重跑不能带断点恢复来源"
    assert kwargs["trigger_source"] == WorkflowRunTriggerEnum.SCHEDULE
    assert kwargs["browser_id"] == BROWSER_ID
    assert kwargs["auth_headers"] == {"x-bili-mid": MID}, "重启后需 mid 头兜底"
    mark.assert_awaited_once_with("old-run", "new-run")


async def test_disabled_workflow_is_skipped(monkeypatch) -> None:
    launch, mark = _mock_common(monkeypatch, enabled=False)

    assert await launcher.relaunch_unstarted_interrupted_runs() == []
    launch.assert_not_awaited()
    mark.assert_not_awaited()


async def test_running_browser_is_skipped(monkeypatch) -> None:
    launch, mark = _mock_common(monkeypatch, running="another-live-run")

    assert await launcher.relaunch_unstarted_interrupted_runs() == []
    launch.assert_not_awaited()
    mark.assert_not_awaited()


async def test_single_failure_does_not_block_other_candidates(monkeypatch) -> None:
    monkeypatch.setattr(settings, "workflow_auto_restart_unstarted_interrupted", True)
    monkeypatch.setattr(
        launcher.workflow_run_crud_svr,
        "list_unstarted_interrupted",
        AsyncMock(return_value=[_fake_run("bad"), _fake_run("good")]),
    )

    calls: list[str] = []

    async def _launch(workflow, **kwargs):
        calls.append(kwargs.get("trigger_source"))
        if len(calls) == 1:
            raise RuntimeError("launch boom")
        return "new-run-2"

    monkeypatch.setattr(launcher, "launch_workflow_run", _launch)
    monkeypatch.setattr(
        launcher.workflow_run_crud_svr,
        "mark_auto_restarted",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        launcher.workflow_crud_svr,
        "get_by_workflow_id",
        AsyncMock(
            return_value=SimpleNamespace(
                workflow_id=WORKFLOW_ID,
                browser_id=BROWSER_ID,
                mid=MID,
                is_enabled=True,
            )
        ),
    )
    monkeypatch.setattr(
        launcher.workflow_run_crud_svr,
        "get_running_for_browser",
        AsyncMock(return_value=None),
    )

    assert await launcher.relaunch_unstarted_interrupted_runs() == ["new-run-2"]
    assert len(calls) == 2
