#!/usr/bin/env python3
"""把动作 JSON 里的裸 ``selector`` 迁移成 ``locator`` 定位器结构。

背景
----
历史上元素级动作的参数只有 ``selector: str``（单条 CSS 字符串），无法表达定位策略、
多命中选择、作用域、候选链。现在统一改为 ``LocatorSpec``：

    "selector": "#btn"            →  "locator": {"value": "#btn"}
    "item_selector": ".item"      →  "item_locator": {"value": ".item"}
    "exclude_selector": ".top"    →  "exclude_locator": {"value": ".top"}
    "exclude_text_selector": ".n" →  "exclude_text_locator": {"value": ".n"}
    "trigger_selector": "#go"     →  "trigger_locator": {"value": "#go"}

映射是**机械的**（单条 CSS 原样搬进 ``locator.value``），不改变任何选择器语义、
不擅自把逗号并集改成 fallbacks（那是行为变化，应由人工按需改写）。

用法::

    # 预演
    .venv/bin/python scripts/migrate_action_json_to_locator.py --dry-run

    # 原地迁移（自动备份到 actions/.backup/）
    .venv/bin/python scripts/migrate_action_json_to_locator.py --write
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTIONS_DIR = PROJECT_ROOT / "actions"
BACKUP_DIR = ACTIONS_DIR / ".backup"

#: 旧字段名 → 新字段名（值是 LocatorSpec 对象）
_KEY_MAP = {
    "selector": "locator",
    "item_selector": "item_locator",
    "exclude_selector": "exclude_locator",
    "exclude_text_selector": "exclude_text_locator",
    "trigger_selector": "trigger_locator",
}


def migrate_node(node: Any, stats: dict[str, int]) -> Any:
    """递归迁移一个 JSON 节点（dict / list / 标量）"""
    if isinstance(node, list):
        return [migrate_node(item, stats) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        new_key = _KEY_MAP.get(key)
        if new_key is None:
            out[key] = migrate_node(value, stats)
            continue

        legacy = value.strip() if isinstance(value, str) else ""
        if not legacy:
            # 空串等价于「没有定位器」，直接丢弃该键
            stats[f"{key}->dropped"] += 1
            continue
        if new_key in node:
            # 已经迁移过（或人工写了新结构）→ 以新结构为准
            stats[f"{key}->skipped"] += 1
            continue
        out[new_key] = {"value": legacy}
        stats[f"{key}->{new_key}"] += 1
    return out


def iter_paths(steps: Any):
    """递归产出步骤列表（含分支 / 循环体里的子步骤）"""
    if isinstance(steps, list):
        for step in steps:
            yield step
            yield from iter_paths(step)
    elif isinstance(steps, dict):
        for value in steps.values():
            if isinstance(value, (list, dict)):
                yield from iter_paths(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="动作 JSON selector → locator 迁移")
    parser.add_argument("--write", action="store_true", help="写入文件（默认只预演）")
    parser.add_argument("--dry-run", action="store_true", help="只预演，不写文件（默认）")
    args = parser.parse_args()
    write = args.write and not args.dry_run

    files = sorted(ACTIONS_DIR.glob("action_*.json"))
    if not files:
        raise SystemExit(f"没有找到动作 JSON: {ACTIONS_DIR}/action_*.json")

    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        stats: dict[str, int] = {}
        for key in _KEY_MAP:
            stats.setdefault(f"{key}->{_KEY_MAP[key]}", 0)
            stats.setdefault(f"{key}->skipped", 0)
            stats.setdefault(f"{key}->dropped", 0)
        migrated = migrate_node(raw, stats)
        changed = {k: v for k, v in stats.items() if v}

        print(f"[file] {path.name}  {changed or '无需迁移'}")
        if not write or not changed:
            continue

        BACKUP_DIR.mkdir(exist_ok=True)
        shutil.copy2(path, BACKUP_DIR / path.name)
        path.write_text(
            json.dumps(migrated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  [backup] {BACKUP_DIR / path.name}")
        print(f"  [write]  {path}")

    if not write:
        print("\n[dry-run] 未写入文件；确认无误后加 --write 执行。")


if __name__ == "__main__":
    main()
