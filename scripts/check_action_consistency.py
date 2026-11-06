"""内置动作一致性门禁（有差异即以退出码 1 结束）

断言 7 件事（对应 ``docs/动作补齐与MCP同步实施计划书.md`` P6）：

1. ``len(BuiltinActionType) == len(Name) == len(Desc)``，且每个 type 都有 Name/Desc；
2. 每个 type 都有 Icon（历史缺成员的 3 个动作在 ``ICON_ALLOWLIST`` 里显式列出）；
3. Icon 编号唯一（前端按编号找素材，重复会导致图标错乱）；
4. ``get_all_actions_metadata()`` 条目数 == type 数，且每条 name/desc/params_schema 非空；
5. ``set(type) - set(BUILTIN_ACTION_MAP) == {"composite"}``（其余内置动作都必须有注册类）；
6. 所有 ``actions/*.json`` 能通过 ``workflow_step_adapter.validate_python`` +
   ``PipelineBuilder.build``（只读，不落库）；
7. 打印 MCP 侧期望的动作清单（供人工比对 ``rpa_debug_actions_list``）。

用法::

    .venv/bin/python scripts/check_action_consistency.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.execution.action_params import (  # noqa: E402
    BuiltinActionDesc,
    BuiltinActionIconId,
    BuiltinActionName,
    BuiltinActionType,
    _ensure_action_type,
    workflow_step_adapter,
)
from app.services.execution.actions.all_actions import (  # noqa: E402
    BUILTIN_ACTION_MAP,
    get_all_actions_metadata,
)
from app.services.execution.pipeline import PipelineBuilder  # noqa: E402

#: 历史上就没有 Icon 成员的动作（前端会回落到内置图标）。
#: 只允许这 3 个例外；新增动作必须补 BuiltinActionIconId。
#: 用枚举**成员名**（大写）比对，与 BuiltinActionName/Desc/IconId 的 name 口径一致。
ICON_ALLOWLIST: frozenset[str] = frozenset({"OPEN_PAGE", "CLOSE_PAGE", "PARALLEL"})

#: 没有注册动作类的内置类型（复合动作由自定义动作表驱动，不需要内置类）。
NO_CLASS_ALLOWLIST: frozenset[str] = frozenset({"composite"})

ACTIONS_DIR = PROJECT_ROOT / "actions"


class ConsistencyError(Exception):
    """一致性检查失败（收集后统一打印）。"""


def _check_enum_tables(problems: list[str]) -> None:
    """1-3：Name/Desc/Icon 三张展示表与 Type 对齐，且 Icon 编号唯一。"""
    types = {member.name for member in BuiltinActionType}
    names = {member.name for member in BuiltinActionName}
    descs = {member.name for member in BuiltinActionDesc}
    icons = {member.name for member in BuiltinActionIconId}

    if not (len(types) == len(names) == len(descs)):
        problems.append(
            f"数量不一致: type={len(types)} name={len(names)} desc={len(descs)}"
        )

    for label, others in (("BuiltinActionName", names), ("BuiltinActionDesc", descs)):
        missing = sorted(types - others)
        extra = sorted(others - types)
        if missing:
            problems.append(f"{label} 缺少成员: {missing}")
        if extra:
            problems.append(f"{label} 存在多余成员（无对应 type）: {extra}")

    missing_icons = sorted(types - icons - ICON_ALLOWLIST)
    if missing_icons:
        problems.append(
            f"BuiltinActionIconId 缺少成员（非 allowlist）: {missing_icons}"
        )
    stray_allowlisted = sorted(ICON_ALLOWLIST & icons)
    if stray_allowlisted:
        problems.append(
            f"ICON_ALLOWLIST 中的动作已补上 Icon，请从 allowlist 移除: {stray_allowlisted}"
        )

    used: dict[int, str] = {}
    for member in BuiltinActionIconId:
        value = int(member.value)
        if value in used:
            problems.append(
                f"Icon 编号重复: {value} 同时被 {used[value]} 与 {member.name} 占用"
            )
        used[value] = member.name


def _check_metadata(problems: list[str]) -> list:
    """4：元数据条目数与其他字段完整性。"""
    metadata = get_all_actions_metadata()
    types = list(BuiltinActionType)
    if len(metadata) != len(types):
        problems.append(f"元数据条目数 {len(metadata)} != type 数 {len(types)}")

    by_id = {str(item.id.value): item for item in metadata}
    for member in types:
        item = by_id.get(member.value)
        if item is None:
            problems.append(f"元数据缺少动作: {member.value}")
            continue
        if not item.name.strip():
            problems.append(f"元数据 name 为空: {member.value}")
        if not item.description.strip():
            problems.append(f"元数据 description 为空: {member.value}")
        if not item.json_schema:
            problems.append(f"元数据 params_schema 为空: {member.value}")
    return metadata


def _check_registry(problems: list[str]) -> None:
    """5：除允许的类型外，每个内置动作都必须有已注册的动作类。"""
    registered = set(BUILTIN_ACTION_MAP)
    types = {member.value for member in BuiltinActionType}
    diff = types - registered - NO_CLASS_ALLOWLIST
    if diff:
        problems.append(
            f"以下动作缺少 register_action_class 注册（all_actions 导出）: {sorted(diff)}"
        )
    unknown = sorted(registered - types)
    if unknown:
        problems.append(f"注册表中存在未在 BuiltinActionType 声明的动作: {unknown}")


def _check_action_jsons(problems: list[str]) -> int:
    """6：所有动作 JSON 都能反序列化 + 编译（只读校验，不改任何 JSON/DB）。"""
    if not ACTIONS_DIR.is_dir():
        problems.append(f"动作目录不存在: {ACTIONS_DIR}")
        return 0

    checked = 0
    for path in sorted(ACTIONS_DIR.glob("*.json")):
        if path.name.endswith(".bak") or ".bak_" in path.name:
            continue
        checked += 1
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - JSON 本身坏了也要报出来
            problems.append(f"{path.name}: JSON 解析失败: {exc}")
            continue

        steps = raw.get("steps")
        if not isinstance(steps, list) or not steps:
            problems.append(f"{path.name}: 缺少非空 steps 数组")
            continue
        try:
            normalized = _ensure_action_type(json.loads(json.dumps(steps)))
            nodes = [workflow_step_adapter.validate_python(step) for step in normalized]
        except Exception as exc:  # noqa: BLE001 - 结构校验失败即门禁失败
            problems.append(f"{path.name}: 步骤校验失败: {type(exc).__name__}: {exc}")
            continue
        try:
            PipelineBuilder.build(nodes)
        except Exception as exc:  # noqa: BLE001 - 编译失败即门禁失败
            problems.append(f"{path.name}: Pipeline 编译失败: {type(exc).__name__}: {exc}")
    return checked


def _print_mcp_expectation(metadata: list) -> None:
    """7：打印 MCP 侧期望的动作清单（供人工比对 rpa_debug_actions_list）。"""
    print("\n[MCP] 期望动作清单（rpa_debug_actions_list 应含以下内置动作）：")
    for item in metadata:
        print(f"  - {item.id.value:<20} {item.name:<8} icon={item.icon_series}/{item.icon_id}")


def main() -> int:
    problems: list[str] = []

    _check_enum_tables(problems)
    metadata = _check_metadata(problems)
    _check_registry(problems)
    checked_jsons = _check_action_jsons(problems)

    types_count = len(list(BuiltinActionType))
    meta_count = len(metadata)
    print(f"types={types_count} meta={meta_count}")
    print(
        f"name={len(list(BuiltinActionName))} desc={len(list(BuiltinActionDesc))} "
        f"icon={len(list(BuiltinActionIconId))} registered={len(BUILTIN_ACTION_MAP)}"
    )
    print(f"action_json_checked={checked_jsons}")

    _print_mcp_expectation(metadata)

    if problems:
        print("\n[FAIL] 发现以下不一致：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print("\n[OK] 内置动作类型 / 名称 / 描述 / 图标 / 元数据 / 注册类 / 动作 JSON 全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
