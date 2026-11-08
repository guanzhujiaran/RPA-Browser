#!/usr/bin/env python3
"""动作步骤清理：按定位器能力去掉冗余等待 / 把「并集选择器」改成候选链。

定位器（``locator`` + ``fallbacks``）落地后，历史步骤里有三类东西变成纯冗余：

1. **相邻的重复 wait**：同一 ``locator.value`` 连续等两次（编辑残留）；
2. **被 ``trigger_locator`` 取代的 wait**：``wait_for_response`` 自带「先等元素、再点击、
   再等响应」，前面那个 wait 纯属白等；
3. **并集 CSS 冒充优先级链**：``get_text`` 用 ``.a, .b, .c`` 只能按 DOM 顺序取第一个，
   表达不了「哪种布局在就用哪个」；改成 ``locator`` + ``fallbacks`` 后语义才正确。

另外把「wait(attached) + element_state(count, timeout=0)」合并成一步：``element_state``
本来就支持等待（``timeout`` 只用于等 attached），两步一步做。

规则都是**结构匹配**，不写死某条业务步骤；``--dry-run`` 默认只打印。

用法::

    .venv/bin/python scripts/opt_action_locators.py --dry-run
    .venv/bin/python scripts/opt_action_locators.py --write     # 改 actions/*.json
    .venv/bin/python scripts/opt_action_locators.py --db        # 改库里的 steps
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:  # 允许直接执行
    sys.path.insert(0, str(PROJECT_ROOT))

ACTIONS_DIR = PROJECT_ROOT / "actions"

#: 步骤里的分支容器键（子步骤可能挂在 step 下，也可能挂在 step.params 下）
_BRANCH_KEYS = ("loopBranch", "TrueBranch", "FalseBranch")

#: 只有「收集型读取」动作才把并集改候选链：``get_text`` 收集全部命中文本，
#: 输出变量取第 1 个 = DOM 顺序，表达不了优先级。而 ``wait``/``element_state``
#: 上的并集是「任一存在即算命中」，语义正确，不能改。
_UNION_AS_FALLBACK_ACTIONS = frozenset({"get_text"})

#: 可以合并进 ``element_state`` 的 wait 状态（``element_state`` 只能等 attached）
_MERGEABLE_WAIT_STATE = "attached"


@dataclass(slots=True)
class OptStats:
    """单份步骤树的改动计数"""

    hunks: Counter = field(default_factory=Counter)

    def add(self, rule: str, detail: str) -> None:
        self.hunks[f"{rule}: {detail}"] += 1

    def render(self) -> str:
        if not self.hunks:
            return "无需改动"
        return "; ".join(f"{k} ×{v}" for k, v in self.hunks.items())


def _params(step: dict) -> dict:
    params = step.get("params")
    return params if isinstance(params, dict) else {}


def _locator_value(step: dict, key: str = "locator") -> str | None:
    locator = _params(step).get(key)
    if isinstance(locator, dict):
        value = locator.get("value")
        if isinstance(value, str):
            return value.strip()
    return None


def _split_top_level(value: str) -> list[str]:
    """按顶层逗号切分 CSS 选择器列表（引号 / 括号内的逗号不切）"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for char in value:
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            buf.append(char)
            continue
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        if char == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(char)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _recurse(step: dict, stats: OptStats) -> None:
    """先递归处理分支里的子步骤列表"""
    for key in _BRANCH_KEYS:
        child = _params(step).get(key) or step.get(key)
        if isinstance(child, list) and child:
            step_params = _params(step)
            target_container = step_params if key in step_params else step
            target_container[key] = transform_steps(child, stats)


def _drop_duplicate_waits(steps: list[dict], stats: OptStats) -> list[dict]:
    """规则 1：相邻两个同定位器的 wait，保留超时更长的那个"""
    out: list[dict] = []
    for step in steps:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.get("action_id") == "wait"
            and step.get("action_id") == "wait"
            and _locator_value(prev)
            and _locator_value(prev) == _locator_value(step)
            and not prev.get("output_vars")
            and not step.get("output_vars")
        ):
            kept_timeout = max(
                int(_params(prev).get("timeout") or 0),
                int(_params(step).get("timeout") or 0),
            )
            new_params = {**_params(prev), **_params(step), "timeout": kept_timeout}
            out[-1] = {**step, "params": new_params}
            stats.add("重复wait", _locator_value(step) or "")
            continue
        out.append(step)
    return out


def _drop_waits_before_trigger(steps: list[dict], stats: OptStats) -> list[dict]:
    """规则 2：被 ``wait_for_response.trigger_locator`` 取代的 wait"""
    drop: set[int] = set()
    for index, step in enumerate(steps[:-1]):
        if step.get("action_id") != "wait":
            continue
        value = _locator_value(step)
        if not value:
            continue
        nxt = steps[index + 1]
        if nxt.get("action_id") != "wait_for_response":
            continue
        if _locator_value(nxt, "trigger_locator") != value:
            continue
        trigger_timeout = int(_params(nxt).get("trigger_timeout") or 0)
        wait_timeout = int(_params(step).get("timeout") or 0)
        if trigger_timeout < wait_timeout:
            continue  # 触发点击的等待预算更小，删掉会缩短总等待，保守跳过
        drop.add(index)
        stats.add("trigger已等待", value)
    return [step for index, step in enumerate(steps) if index not in drop]


def _merge_wait_into_element_state(steps: list[dict], stats: OptStats) -> list[dict]:
    """规则 3：wait(attached) + element_state(timeout=0) 合成一步"""
    out: list[dict] = []
    for step in steps:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.get("action_id") == "wait"
            and step.get("action_id") == "element_state"
            and _params(prev).get("state", "visible") == _MERGEABLE_WAIT_STATE
            and int(_params(step).get("timeout") or 0) == 0
            and _locator_value(prev)
            and _locator_value(prev) == _locator_value(step)
            and not prev.get("output_vars")
        ):
            wait_timeout = int(_params(prev).get("timeout") or 0)
            out[-1] = {
                **step,
                "params": {**_params(step), "timeout": wait_timeout},
            }
            stats.add("wait并入element_state", _locator_value(step) or "")
            continue
        out.append(step)
    return out


def _union_to_fallbacks(steps: list[dict], stats: OptStats) -> list[dict]:
    """规则 4：收集型读取动作的并集选择器 → 候选链"""
    for step in steps:
        if step.get("action_id") not in _UNION_AS_FALLBACK_ACTIONS:
            continue
        params = _params(step)
        locator = params.get("locator")
        if not isinstance(locator, dict):
            continue
        value = locator.get("value")
        if not isinstance(value, str) or "," not in value:
            continue
        parts = _split_top_level(value)
        if len(parts) < 2:
            continue
        primary, *rest = parts
        fallbacks = list(params.get("fallbacks") or [])
        fallbacks.extend({**locator, "value": item} for item in rest)
        params["locator"] = {**locator, "value": primary}
        params["fallbacks"] = fallbacks
        stats.add("并集改候选链", f"{value} → {primary}")
    return steps


def transform_steps(steps: list[dict], stats: OptStats) -> list[dict]:
    """对一份步骤列表应用全部规则（递归处理子分支）"""
    for step in steps:
        _recurse(step, stats)
    steps = _drop_duplicate_waits(steps, stats)
    steps = _drop_waits_before_trigger(steps, stats)
    steps = _merge_wait_into_element_state(steps, stats)
    return _union_to_fallbacks(steps, stats)


def _opt_json_files(*, write: bool) -> None:
    for path in sorted(ACTIONS_DIR.glob("action_*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        stats = OptStats()
        before = len(json.dumps(raw, ensure_ascii=False))
        raw["steps"] = transform_steps(raw["steps"], stats)
        after = len(json.dumps(raw, ensure_ascii=False))
        print(f"[json] {path.name}: {stats.render()} (体积 {before} → {after})")
        if not write or not stats.hunks:
            continue
        backup = path.with_suffix(path.suffix + ".bak_20261109_locate_opt")
        shutil.copy2(path, backup)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  [backup] {backup}")


async def _opt_db(*, write: bool) -> None:
    from sqlmodel import select

    from app.models.database.workflow.models import CompositeActionModel
    from app.utils.depends.session_manager import DatabaseSessionManager

    async with DatabaseSessionManager.async_session() as session:
        rows = list((await session.exec(select(CompositeActionModel))).all())
        for row in rows:
            stats = OptStats()
            migrated = transform_steps(json.loads(json.dumps(row.steps)), stats)
            print(f"[db] id={row.id} {row.name!r}: {stats.render()}")
            if not write or not stats.hunks:
                continue
            row.steps = migrated
            session.add(row)
        if write:
            await session.commit()
            print("\n[done] 已写入数据库；请重算内容指纹：")
            print("       .venv/bin/python scripts/backfill_action_content_hash.py")
        else:
            await session.rollback()
            print("\n[dry-run] 已回滚")


def main() -> None:
    parser = argparse.ArgumentParser(description="动作 steps 定位器化清理")
    parser.add_argument("--write", action="store_true", help="写 actions/*.json")
    parser.add_argument("--db", action="store_true", help="改数据库里的 steps")
    parser.add_argument("--dry-run", action="store_true", help="只打印（默认）")
    args = parser.parse_args()

    if args.db:
        asyncio.run(_opt_db(write=args.write and not args.dry_run))
        return
    _opt_json_files(write=args.write and not args.dry_run)


if __name__ == "__main__":
    main()
