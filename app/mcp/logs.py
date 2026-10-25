"""步骤日志整理：把 ``ActionLogRecord`` 链路压缩成「能直接改动作」的报告。

``ActionLogRecord`` 只记录调用深度（``depth``）与父批次，没有绝对 step 路径，
因此这里用「写入顺序 + depth + 动作名」还原调用树，并把失败项映射为
``steps[i]`` / ``steps[i].children[...]`` 的**候选**路径，无法唯一定位时标 ``ambiguous``。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.mcp.models import RunLogReport, RunRecovery, StatusCount


class ActionLogItem(BaseModel):
    """日志列表项（与 ``ActionLogItemResponse`` 对齐）。"""

    log_id: str = ""
    execution_id: str = ""
    parent_execution_id: str | None = None
    depth: int = 0
    action_id: str = ""
    action_name: str = ""
    action_type: str = ""
    source: str = ""
    workflow_id: str | None = None
    browser_id: str = ""
    page_url: str = ""
    status: str = ""
    success: bool = False
    error_message: str | None = None
    execution_time: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class ActionLogDetail(ActionLogItem):
    """日志详情（附带入参 / 结果 / 变量 / 步骤内部日志）。"""

    params: JsonValue = None
    result_data: JsonValue = None
    variables: JsonValue = None
    logs: list[str] = Field(default_factory=list)


def _shorten(value: JsonValue, max_chars: int) -> JsonValue:
    """大字段截断：只对超长字符串做处理，结构保持不变。"""
    if isinstance(value, str) and len(value) > max_chars:
        return f"{value[:max_chars]}…(截断，共 {len(value)} 字符)"
    return value


def _shorten_deep(value: JsonValue, max_chars: int) -> JsonValue:
    """递归截断嵌套结构里的长字符串。"""
    if isinstance(value, str):
        return _shorten(value, max_chars)
    if isinstance(value, list):
        return [_shorten_deep(item, max_chars) for item in value]
    if isinstance(value, dict):
        return {key: _shorten_deep(item, max_chars) for key, item in value.items()}
    return value


def _format_item(index: int, item: ActionLogItem, max_chars: int) -> str:
    """单行链路文本：缩进 + 状态 + 动作 + 耗时 + 错误。"""
    indent = "  " * max(item.depth, 0)
    mark = "OK " if item.success else "ERR"
    line = (
        f"{indent}[{index}] {mark} {item.action_name or item.action_id}"
        f"({item.action_id}) {item.execution_time * 1000:.0f}ms"
    )
    if item.page_url:
        line += f" url={_shorten(item.page_url, 120)}"
    if item.error_message:
        line += f" | {_shorten(item.error_message, max_chars)}"
    return line


def build_recovery_hints(items: list[ActionLogItem]) -> list[RunRecovery]:
    """为失败项推断「应改哪个步骤」的候选路径。"""
    hints: list[RunRecovery] = []
    name_counts: dict[str, int] = {}
    for item in items:
        name_counts[item.action_name or item.action_id] = (
            name_counts.get(item.action_name or item.action_id, 0) + 1
        )

    #: 最近一个顶层步骤的序号（深层失败的父步骤只能定位到它）
    current_top_index: int | None = None

    for item in items:
        if item.depth == 0:
            current_top_index = _top_index(items, item)
            if not item.success:
                hints.append(
                    _make_hint(
                        item,
                        path=f"steps[{current_top_index}]",
                        order=current_top_index,
                        ambiguous=name_counts.get(item.action_name or item.action_id, 0) > 1,
                    )
                )
            continue

        if not item.success:
            path = (
                f"steps[{current_top_index}].children[...]"
                if current_top_index is not None
                else "steps[...].children[...]"
            )
            hints.append(
                _make_hint(
                    item,
                    path=path,
                    order=item.depth,
                    # 循环体内每轮都会重复同名动作，必然无法只凭日志唯一定位
                    ambiguous=True,
                )
            )
    return hints


def _top_index(items: list[ActionLogItem], target: ActionLogItem) -> int:
    """该顶层日志项在「全部顶层项」中的序号。"""
    index = 0
    for item in items:
        if item is target:
            return index
        if item.depth == 0:
            index += 1
    return index


def _make_hint(
    item: ActionLogItem, *, path: str, order: int, ambiguous: bool
) -> RunRecovery:
    return RunRecovery(
        action_name=item.action_name or item.action_id,
        action_id=item.action_id,
        order=order,
        depth=item.depth,
        candidate_path=path,
        ambiguous=ambiguous,
        error_message=item.error_message or "",
    )


def summarize_run_logs(
    execution_id: str,
    items: list[ActionLogItem],
    total: int,
    details: dict[str, ActionLogDetail],
    *,
    truncated: bool,
    max_chars: int,
) -> RunLogReport:
    """汇总成 MCP 返回结构（树文本 + 失败明细 + 改哪一步的提示）。"""
    counters: dict[str, int] = {}
    for item in items:
        key = item.status or ("success" if item.success else "failed")
        counters[key] = counters.get(key, 0) + 1
    status_counts = [
        StatusCount(status=key, count=count) for key, count in counters.items()
    ]

    tree = "\n".join(_format_item(index, item, max_chars) for index, item in enumerate(items))

    failed: list[JsonValue] = []
    for item in items:
        if item.success:
            continue
        detail = details.get(item.log_id)
        failed.append(
            {
                "log_id": item.log_id,
                "depth": item.depth,
                "action_id": item.action_id,
                "action_name": item.action_name,
                "action_type": item.action_type,
                "error_message": _shorten(item.error_message or "", max_chars),
                "page_url": item.page_url,
                "params": _shorten_deep(
                    detail.params if detail is not None else None, max_chars
                ),
                "result_data": _shorten_deep(
                    detail.result_data if detail is not None else None, max_chars
                ),
                "step_logs": [
                    _shorten(line, max_chars)
                    for line in (detail.logs[-20:] if detail is not None else [])
                ],
            }
        )

    return RunLogReport(
        execution_id=execution_id,
        total=total,
        fetched=len(items),
        truncated=truncated,
        status_counts=status_counts,
        tree=tree,
        failed=failed,
        recovery_hints=build_recovery_hints(items),
    )
