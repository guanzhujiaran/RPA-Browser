#!/usr/bin/env python3
"""把数据库里存量动作步骤的 ``selector`` 迁移成 ``locator`` 定位器结构。

与 ``scripts/migrate_action_json_to_locator.py`` 的映射规则**完全一致**
（共用同一个 ``migrate_node``），区别只在于作用对象：

- 前者改 ``actions/*.json``（可读源头）；
- 本脚本改 ``compositeactionmodel.steps``（执行时真正读的库表），
  并且覆盖 **Fork 动作**（steps 里的复合动作引用 ID 已被改写，不能拿上游 JSON 覆盖）。

为什么直接改库而不走 ``custom-actions/update`` 接口：接口会把 steps 反序列化成
参数模型再回写，跑在**旧代码**上的服务端不认 ``locator`` 字段，会把新结构丢掉。
这里在字节层面做机械替换，只替换 ``selector`` 相关的键，其余内容（含 ID 改写）原样保留。

迁移后必须重算内容指纹（``content_hash`` / ``last_synced_hash``）：::

    .venv/bin/python scripts/backfill_action_content_hash.py

用法::

    # 预演（打印每个动作的替换计数，最后 rollback）
    .venv/bin/python scripts/migrate_db_steps_to_locator.py --dry-run

    # 正式执行
    .venv/bin/python scripts/migrate_db_steps_to_locator.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:  # 允许直接执行
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import select  # noqa: E402

from app.models.database.workflow.models import CompositeActionModel  # noqa: E402
from app.utils.depends.session_manager import DatabaseSessionManager  # noqa: E402
from scripts.migrate_action_json_to_locator import _KEY_MAP, migrate_node  # noqa: E402


def _empty_stats() -> dict[str, int]:
    stats: dict[str, int] = {}
    for key in _KEY_MAP:
        stats[f"{key}->{_KEY_MAP[key]}"] = 0
        stats[f"{key}->skipped"] = 0
        stats[f"{key}->dropped"] = 0
    return stats


async def _run(*, dry_run: bool) -> None:
    async with DatabaseSessionManager.async_session() as session:
        rows = (await session.exec(select(CompositeActionModel))).all()

    targets = [row for row in rows if _contains_legacy(row.steps)]
    print(f"[scan] 共 {len(rows)} 个动作，含 selector 的 {len(targets)} 个")
    if not targets:
        return

    async with DatabaseSessionManager.async_session() as session:
        for row in targets:
            stats = _empty_stats()
            migrated = migrate_node(json.loads(json.dumps(row.steps)), stats)
            changed = {k: v for k, v in stats.items() if v}
            print(
                f"[row] id={row.id} action_id={row.action_id} mid={row.mid} "
                f"name={row.name!r} {changed}"
            )
            if dry_run or not changed:
                continue
            db_row = await session.get(CompositeActionModel, row.id)
            if db_row is None:
                print(f"  [skip] 行 {row.id} 已不存在")
                continue
            db_row.steps = migrated
            session.add(db_row)
        if dry_run:
            await session.rollback()
            print("\n[dry-run] 已回滚，未写入数据库")
        else:
            await session.commit()
            print("\n[done] 已写入数据库；下一步请重算内容指纹：")
            print("       .venv/bin/python scripts/backfill_action_content_hash.py")


def _contains_legacy(steps: object) -> bool:
    """步骤树里是否还有旧字段（避免重复迁移）"""
    text = json.dumps(steps or [], ensure_ascii=False)
    return any(f'"{key}"' in text for key in _KEY_MAP)


def main() -> None:
    parser = argparse.ArgumentParser(description="数据库步骤 selector → locator 迁移")
    parser.add_argument("--dry-run", action="store_true", help="只预演并回滚")
    args = parser.parse_args()
    asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
