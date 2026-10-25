"""工作流运行状态机单测（docs/工作流运行状态机计划书.md §5）

纯规则测试：不依赖数据库 / 事件循环，只验证迁移表与校验函数。
"""

from itertools import permutations

import pytest

from app.models.database.workflow.models import WorkflowRunStatusEnum as S
from app.services.execution.run_state_machine import (
    ALLOWED_TRANSITIONS,
    INITIAL_STATUSES,
    TERMINAL_STATUSES,
    WorkflowRunStateError,
    can_transition,
    ensure_initial_status,
    ensure_transition,
    is_terminal,
    normalize_status,
)

ALL_STATUSES = list(S)


def test_transition_table_covers_all_statuses() -> None:
    """迁移表必须覆盖枚举里的每一个状态（新增状态时强制补规则）"""
    assert set(ALLOWED_TRANSITIONS) == set(ALL_STATUSES)
    assert set(INITIAL_STATUSES) == {S.QUEUED, S.RUNNING}
    assert TERMINAL_STATUSES == {S.SUCCESS, S.FAILED, S.INTERRUPTED, S.CANCELLED}


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.QUEUED, S.RUNNING),
        (S.QUEUED, S.CANCELLED),
        (S.QUEUED, S.FAILED),
        (S.QUEUED, S.INTERRUPTED),
        (S.RUNNING, S.SUCCESS),
        (S.RUNNING, S.FAILED),
        (S.RUNNING, S.INTERRUPTED),
        (S.RUNNING, S.CANCELLED),
    ],
)
def test_legal_transitions(current: S, target: S) -> None:
    assert can_transition(current, target)
    assert ensure_transition(current, target, run_id="r1") is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.SUCCESS, S.RUNNING),
        (S.SUCCESS, S.FAILED),
        (S.FAILED, S.SUCCESS),
        (S.INTERRUPTED, S.RUNNING),
        (S.CANCELLED, S.RUNNING),
        (S.QUEUED, S.SUCCESS),
        (S.RUNNING, S.QUEUED),
    ],
)
def test_illegal_transitions_raise(current: S, target: S) -> None:
    assert not can_transition(current, target)
    with pytest.raises(WorkflowRunStateError) as exc_info:
        ensure_transition(current, target, run_id="r1")
    assert exc_info.value.run_id == "r1"
    assert exc_info.value.current == current
    assert exc_info.value.target == target


def test_all_terminal_statuses_reject_every_other_status() -> None:
    for terminal, target in permutations(ALL_STATUSES, 2):
        if terminal not in TERMINAL_STATUSES or target == terminal:
            continue
        assert not can_transition(terminal, target)


def test_same_status_is_idempotent() -> None:
    for status in ALL_STATUSES:
        assert can_transition(status, status)
        assert ensure_transition(status, status) is status


def test_is_terminal() -> None:
    assert is_terminal(S.SUCCESS)
    assert is_terminal(S.CANCELLED)
    assert not is_terminal(S.QUEUED)
    assert not is_terminal(S.RUNNING)


def test_ensure_initial_status() -> None:
    assert ensure_initial_status(S.QUEUED) is S.QUEUED
    assert ensure_initial_status("running") is S.RUNNING
    with pytest.raises(WorkflowRunStateError):
        ensure_initial_status(S.SUCCESS)
    with pytest.raises(WorkflowRunStateError):
        ensure_initial_status(S.CANCELLED)


def test_normalize_status_accepts_str_and_enum() -> None:
    assert normalize_status("queued") is S.QUEUED
    assert normalize_status(S.RUNNING) is S.RUNNING
    with pytest.raises(ValueError):
        normalize_status("not-a-status")
