"""步骤静态校验：只用既有 DSL 适配器 + Pipeline 编译，不执行任何浏览器动作。

校验分两层：
1. **结构层**：``workflow_step_adapter.validate_python`` 逐步骤（含 children）反序列化，
   把 pydantic 的 ``loc`` 转成 ``steps[2].children[0].params.timeout`` 这类可定位路径；
2. **编译层**：``PipelineBuilder.build`` 把（合法的那部分）步骤编译成 Pipeline，
   提前暴露循环 / 分支参数不合法之类的编译期问题。

这样「改 JSON → 校验」完全离线，不必先打浏览器。
"""

from __future__ import annotations

import copy

from collections.abc import Sequence

from loguru import logger
from pydantic import ValidationError

from app.mcp.models import StepIssue, ValidationReport

#: 校验依赖较重（会牵引动作注册表、playwright 等），导入失败时降级为「仅结构不做编译」，
#: 避免 MCP 进程因为环境缺配置直接起不来。
_STATIC_VALIDATION_ERROR: str | None = None
try:
    from app.models.execution.workflow_step_models import (
        _ensure_action_type,
        workflow_step_adapter,
    )
    from app.services.execution.pipeline import PipelineBuilder
except Exception as exc:  # noqa: BLE001 - 环境问题不应让 MCP 无法启动
    _STATIC_VALIDATION_ERROR = f"{type(exc).__name__}: {exc}"
    logger.warning(f"[MCP] 静态校验能力不可用（降级）: {_STATIC_VALIDATION_ERROR}")


def static_validation_available() -> bool:
    """当前进程是否具备静态校验能力。"""
    return _STATIC_VALIDATION_ERROR is None


def _format_path(prefix: str, loc: Sequence[object]) -> str:
    """把 pydantic 的 loc 拼成 JSONPath 风格路径（类名段已在上游剔除）。"""
    if not loc:
        return prefix
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            parts.append(f".{item}")
    return prefix + "".join(parts)


#: discriminated union 的子类名后缀（loc 里会带上，输出时剔除以免路径难读）
_STEP_CLASS_SUFFIX = "WorkflowStep"


def _compact_errors(
    errors: Sequence[object],
) -> list[tuple[list[object], str, str]]:
    """压缩 pydantic 报错：剥掉类名段、合并「action_type 不认识」的重复噪音。"""
    normalized: list[tuple[list[object], str, str]] = []
    invalid_tags = 0
    for error in errors:
        if not isinstance(error, dict):
            continue
        raw_loc = error.get("loc")
        loc: list[object] = []
        if isinstance(raw_loc, (list, tuple)):
            loc = [
                item
                for item in raw_loc
                if not (isinstance(item, str) and item.endswith(_STEP_CLASS_SUFFIX))
            ]
        error_type = str(error.get("type") or "")
        message = str(error.get("msg") or "")
        if (
            error_type in {"literal_error", "union_tag_invalid", "union_tag_not_found"}
            and loc
            and loc[-1] == "action_type"
        ):
            invalid_tags += 1
            continue
        normalized.append((loc, error_type, message))
    if invalid_tags:
        normalized.insert(
            0,
            (
                ["action_type"],
                "invalid_action_type",
                f"未识别的 action_type（{invalid_tags} 个动作类型均不匹配）",
            ),
        )
    return normalized


#: 子步骤可能出现的字段：``children``（模型字段）+ JSON 里实际使用的 ``params.*``
#: （``loop.loopBranch`` / ``if_else.TrueBranch`` / ``if_else.FalseBranch``）。
#: 漏掉 ``params.*`` 会导致「校验通过但子步骤从未被校验」，是真实踩过的坑。
_CHILD_FIELDS: tuple[str, ...] = ("children", "loopBranch", "steps")
_BRANCH_FIELDS: tuple[str, ...] = ("TrueBranch", "FalseBranch")


def _collect_child_steps(raw: object, path: str) -> list[tuple[str, list[object]]]:
    """取出该步骤的所有子步骤（模型字段 + params 内的循环体 / 条件分支）。"""
    if not isinstance(raw, dict):
        return []
    children: list[tuple[str, list[object]]] = []

    for field in _CHILD_FIELDS:
        value = raw.get(field)
        if isinstance(value, list) and value:
            children.append((f"{path}.{field}", value))

    params = raw.get("params")
    if isinstance(params, dict):
        for field in _CHILD_FIELDS + _BRANCH_FIELDS:
            value = params.get(field)
            if isinstance(value, list) and value:
                children.append((f"{path}.params.{field}", value))

    branch = raw.get("on_error_branch")
    if isinstance(branch, dict):
        branch_steps = branch.get("steps")
        if isinstance(branch_steps, list) and branch_steps:
            children.append((f"{path}.on_error_branch.steps", branch_steps))

    return children


def validate_steps(steps: object) -> ValidationReport:
    """校验 steps 列表，返回带定位路径的报告。"""
    if not isinstance(steps, list):
        return ValidationReport(
            ok=False,
            issues=[
                StepIssue(
                    path="steps",
                    message=f"steps 必须是数组，当前是 {type(steps).__name__}",
                    hint="动作 JSON 的 steps 字段应为 [{...}, {...}]",
                )
            ],
        )

    issues: list[StepIssue] = []
    action_ids: list[str] = []
    counter = {"total": 0}
    notes: list[str] = []

    if _STATIC_VALIDATION_ERROR is not None:
        notes.append(f"静态校验已降级（{_STATIC_VALIDATION_ERROR}），仅做 JSON 结构检查")

    def walk(items: list[object], prefix: str) -> list[object]:
        """逐层校验，返回该层校验通过的步骤（用于编译）。"""
        passed: list[object] = []
        for index, raw in enumerate(items):
            counter["total"] += 1
            path = f"{prefix}[{index}]"
            if not isinstance(raw, dict):
                issues.append(
                    StepIssue(
                        path=path,
                        message=f"步骤必须是对象，当前是 {type(raw).__name__}",
                    )
                )
                continue

            action_id = str(raw.get("action_id", ""))
            if prefix == "steps":
                action_ids.append(action_id)

            if _STATIC_VALIDATION_ERROR is not None:
                passed.append(raw)
                for child_prefix, child_items in _collect_child_steps(raw, path):
                    walk(child_items, child_prefix)
                continue

            try:
                normalized = _ensure_action_type(copy.deepcopy(raw))
                node = workflow_step_adapter.validate_python(normalized)
            except ValidationError as exc:
                for loc, error_type, message in _compact_errors(exc.errors()):
                    issues.append(
                        StepIssue(
                            path=_format_path(path, tuple(loc)),
                            action_id=action_id,
                            message=f"{error_type}: {message}",
                            hint=_hint_for(raw, error_type),
                        )
                    )
                # 校验失败也要检查 children，避免漏报
                for child_prefix, child_items in _collect_child_steps(raw, path):
                    walk(child_items, child_prefix)
                continue
            except Exception as exc:  # noqa: BLE001 - 适配器内部异常同样视为该步非法
                issues.append(
                    StepIssue(
                        path=path,
                        action_id=action_id,
                        message=f"{type(exc).__name__}: {exc}",
                        hint=_hint_for(raw, ""),
                    )
                )
                passed.append(raw)
                continue

            passed.append(node)
            for child_prefix, child_items in _collect_child_steps(raw, path):
                walk(child_items, child_prefix)
        return passed

    validated_top = walk(steps, "steps")

    compiled_nodes = 0
    if _STATIC_VALIDATION_ERROR is None and validated_top:
        try:
            pipeline = PipelineBuilder.build(validated_top)
            compiled_nodes = len(pipeline.steps)
        except Exception as exc:  # noqa: BLE001 - 编译期异常即「步骤不合法」
            issues.append(
                StepIssue(
                    path="steps",
                    message=f"Pipeline 编译失败: {type(exc).__name__}: {exc}",
                    hint="重点检查 loop / if_else 的 count、condition、children 结构",
                )
            )
    elif not steps:
        notes.append("steps 为空：保存动作会被服务端拒绝，也无法试跑")

    return ValidationReport(
        ok=not issues,
        step_count=len(steps),
        total_steps=counter["total"],
        action_ids=action_ids,
        issues=issues,
        compiled_nodes=compiled_nodes,
        notes=notes,
    )


def _hint_for(raw: object, error_type: str) -> str:
    """按常见错误给出修复提示。"""
    if not isinstance(raw, dict):
        return ""
    action_id = str(raw.get("action_id", ""))
    #: action_type 缺省时由 action_id 推导（与 _ensure_action_type 一致），提示里显示推导值
    action_type = str(raw.get("action_type") or raw.get("action_id") or "")
    if error_type in {
        "invalid_action_type",
        "union_tag_invalid",
        "union_tag_not_found",
        "literal_error",
    }:
        return (
            f"action_type={action_type!r} 不是已注册的动作类型；内置动作用内置名"
            "（click/input/press/navigate/wait/wait_for_url/wait_for_response/"
            "element_state/select_option/get_text/loop/if_else/print/...），"
            f"复合动作用 composite（action_id={action_id!r} 为雪花 int）"
        )
    if error_type == "missing":
        return "缺少必填参数，检查该步骤 params 中该字段是否为空或未写"
    if "loop" in action_type:
        return "loop 需至少提供 count / loop_while / loop_until 之一，children 为循环体"
    if action_type == "if_else":
        return "if_else 需要 condition 或 expression 二选一，children 承载真分支"
    return ""
