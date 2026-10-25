"""MCP 调试能力的离线单测（不连服务、不开浏览器）。

覆盖：静态校验的路径定位与噪音压缩、日志链路还原与「改哪一步」候选、
落库载荷的校验与字段裁剪。
"""

from __future__ import annotations

import pytest

from app.mcp.logs import ActionLogItem, summarize_run_logs
from app.mcp.save_payload import ActionSavePayload, ActionSavePayloadError
from app.mcp.validator import validate_steps


def test_validate_accepts_print_step() -> None:
    """内置动作的最小合法步骤应通过校验并完成编译。"""
    report = validate_steps([{"action_id": "print", "params": {"message": "hi"}}])

    assert report.ok, report.issues
    assert report.step_count == 1
    assert report.compiled_nodes == 1
    assert report.action_ids == ["print"]


def test_validate_compacts_invalid_action_type() -> None:
    """未知 action_type 应被压缩成一条带定位路径的问题，而不是 22 条噪音。"""
    report = validate_steps([{"action_id": "not_an_action"}])

    assert not report.ok
    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.path == "steps[0].action_type"
    assert "invalid_action_type" in issue.message
    assert "composite" in issue.hint


def test_validate_reports_non_object_step_with_index() -> None:
    """非对象步骤要指到具体下标，便于按提示改 JSON。"""
    report = validate_steps([{"action_id": "print", "params": {}}, "oops"])

    assert not report.ok
    paths = [issue.path for issue in report.issues]
    assert "steps[1]" in paths


def test_validate_reports_children_path() -> None:
    """循环体内部的非法步骤路径应带 .children 前缀。"""
    report = validate_steps(
        [
            {
                "action_id": "loop",
                "params": {"count": 2},
                "children": [{"action_id": "not_an_action"}],
            }
        ]
    )

    assert not report.ok
    assert any(issue.path.startswith("steps[0].children[0]") for issue in report.issues)


def test_validate_warns_on_empty_steps() -> None:
    """空 steps 不报错但要提示（保存会被服务端拒绝）。"""
    report = validate_steps([])

    assert report.ok
    assert any("steps 为空" in note for note in report.notes)


def test_save_payload_drops_db_only_fields() -> None:
    """动作 JSON 里的 DB 冗余字段应被忽略，只保留落库字段。"""
    payload = ActionSavePayload.from_content(
        {
            "name": "demo",
            "steps": [{"action_id": "print", "params": {}}],
            "mid": 140682000,
            "id": 12,
            "created_at": "2026-01-01T00:00:00",
            "likes_count": 3,
        }
    )

    assert payload.name == "demo"
    assert payload.step_action_ids() == ["print"]


def test_save_payload_rejects_empty_steps() -> None:
    """steps 为空必须拒绝落库。"""
    with pytest.raises(ActionSavePayloadError):
        ActionSavePayload.from_content({"name": "demo", "steps": []})


def test_save_payload_rejects_non_object() -> None:
    """顶层不是对象时给出可读错误。"""
    with pytest.raises(ActionSavePayloadError):
        ActionSavePayload.from_content([1, 2, 3])


def test_summarize_run_logs_builds_tree_and_hints() -> None:
    """日志按 depth 缩进还原，失败项给出候选步骤路径。"""
    items = [
        ActionLogItem(
            log_id="1", depth=0, action_id="print", action_name="print", success=True
        ),
        ActionLogItem(
            log_id="2",
            depth=0,
            action_id="click",
            action_name="click",
            success=False,
            error_message="selector not found",
        ),
        ActionLogItem(
            log_id="3",
            depth=1,
            action_id="click",
            action_name="click",
            success=False,
            error_message="inner failed",
        ),
    ]

    report = summarize_run_logs("exec-1", items, 3, {}, truncated=False, max_chars=100)

    assert "[0] OK" in report.tree
    assert "  [2] ERR" in report.tree
    assert {item.status: item.count for item in report.status_counts} == {
        "success": 1,
        "failed": 2,
    }
    paths = [hint.candidate_path for hint in report.recovery_hints]
    assert paths[0] == "steps[1]"
    assert paths[1] == "steps[1].children[...]"
    # 同名动作出现多次，顶层失败也无法唯一定位
    assert report.recovery_hints[0].ambiguous is True
    assert len(report.failed) == 2


def test_validate_walks_params_loop_branch() -> None:
    """循环体在 params.loopBranch 里，非法子步骤必须被定位到（曾漏校验）。"""
    report = validate_steps(
        [
            {
                "action_id": "loop",
                "params": {"count": 1, "loopBranch": [{"action_id": "not_an_action"}]},
            }
        ]
    )

    assert not report.ok
    assert report.total_steps == 2
    assert any(
        issue.path.startswith("steps[0].params.loopBranch[0]") for issue in report.issues
    )


def test_validate_walks_params_if_else_branches() -> None:
    """if_else 的 TrueBranch / FalseBranch 都要递归校验。"""
    report = validate_steps(
        [
            {
                "action_id": "if_else",
                "params": {
                    "expression": "foo == 1",
                    "TrueBranch": [{"action_id": "not_an_action"}],
                    "FalseBranch": ["oops"],
                },
            }
        ]
    )

    assert not report.ok
    paths = [issue.path for issue in report.issues]
    assert any(p.startswith("steps[0].params.TrueBranch[0]") for p in paths)
    assert "steps[0].params.FalseBranch[0]" in paths
