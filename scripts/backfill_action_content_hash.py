#!/usr/bin/env python3
"""回填复合动作的内容指纹（`content_hash` / `last_synced_hash`）

背景
----
`docs/动作版本与内容指纹计划书.md` §3 把「上游是否有新版本」的判定
从 `updated_at` 时间戳改成 `content_hash` 内容指纹。迁移
`20261101_action_content_hash` 只加列**不填数据**，因此上线后必须执行本脚本，
否则：

- 非 Fork 动作 `content_hash` 为空 → 其 Fork 副本永远判不出"有更新"（漏报）；
- Fork 副本 `last_synced_hash` 为空 → 全部显示"上游有新版本"（误报）。

顺序（不可颠倒）
----------------
1. **非 Fork 动作**：按语义内容（steps / parameters_schema / input_vars / output_vars）
   重算 `content_hash`；
2. **Fork 副本**：`content_hash` **继承上游**。原因见计划书 §3.4：Fork / 同步会把
   steps 里的复合动作引用 ID 改写为用户视角的雪花 ID，字节不同但语义等价，
   重算必然与上游不等，会造成"永久有新版本"的误报。多级 Fork 迭代至稳定；
3. **全部动作**：`last_synced_hash = content_hash`（存量视为"当前即最新"），
   避免升级瞬间给所有用户推一条"上游有新版本"的通知。

用法
----
    # 预演：只打印将变更的条目，最后 rollback
    uv run python scripts/backfill_action_content_hash.py --dry-run

    # 正式回填
    uv run python scripts/backfill_action_content_hash.py

注意
----
本脚本**直接连数据库**（不经过 HTTP），需要在能访问 `mysql_browser_info_url` 的环境执行。
幂等：重复执行只会重算并写回相同值。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:  # 允许 `uv run python scripts/xxx.py` 直接跑
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import select  # noqa: E402

from app.models.database.workflow.models import CompositeActionModel  # noqa: E402
from app.utils.action_content_hash import (  # noqa: E402
    compute_content_hash_from_fields,
    short_hash,
)
from app.utils.depends.session_manager import DatabaseSessionManager  # noqa: E402


@dataclass(slots=True)
class BackfillStats:
    """回填结果统计"""

    roots_updated: int = 0
    forks_updated: int = 0
    unchanged: int = 0
    orphans: int = 0

    def render(self) -> str:
        return (
            f"根动作更新 {self.roots_updated} 个，Fork 继承 {self.forks_updated} 个，"
            f"无需变更 {self.unchanged} 个，无法解析上游 {self.orphans} 个"
        )


def _hash_of(model: CompositeActionModel) -> str:
    """按当前落库内容计算语义指纹"""
    return compute_content_hash_from_fields(
        steps=model.steps,
        parameters_schema=model.parameters_schema,
        input_vars=model.input_vars,
        output_vars=model.output_vars,
    )


async def backfill(*, dry_run: bool) -> BackfillStats:
    """执行三步回填；`dry_run=True` 时只改内存并最终 rollback"""
    stats = BackfillStats()

    async with DatabaseSessionManager.async_session() as session:
        result = await session.exec(select(CompositeActionModel))
        models: list[CompositeActionModel] = list(result.all())
        by_id: dict[int, CompositeActionModel] = {
            model.id: model for model in models if model.id is not None
        }

        # ── 步骤 1：非 Fork 动作重算自身指纹 ──
        for model in models:
            if model.forked_from_id is not None:
                continue
            new_hash = _hash_of(model)
            if model.content_hash == new_hash:
                stats.unchanged += 1
                continue
            print(
                f"[root] action_id={model.action_id} {model.name!r} "
                f"{short_hash(model.content_hash) or '-'} -> {short_hash(new_hash)}"
            )
            model.content_hash = new_hash
            stats.roots_updated += 1

        # ── 步骤 2：Fork 副本继承上游（多级 Fork 迭代到稳定） ──
        pending = [model for model in models if model.forked_from_id is not None]
        while pending:
            remaining: list[CompositeActionModel] = []
            progressed = False
            for model in pending:
                upstream = by_id.get(model.forked_from_id) if model.forked_from_id else None
                if upstream is None or not upstream.content_hash:
                    remaining.append(model)
                    continue
                if model.content_hash != upstream.content_hash:
                    print(
                        f"[fork] action_id={model.action_id} {model.name!r} "
                        f"{short_hash(model.content_hash) or '-'} -> "
                        f"{short_hash(upstream.content_hash)}"
                    )
                    model.content_hash = upstream.content_hash
                    stats.forks_updated += 1
                else:
                    stats.unchanged += 1
                progressed = True
            if not progressed:
                stats.orphans += len(remaining)
                print(
                    f"[warn] {len(remaining)} 个 Fork 无法解析上游"
                    "（上游已删除或成环），保持原值"
                )
                break
            pending = remaining

        # ── 步骤 3：同步快照对齐自身指纹（存量视为当前即最新） ──
        for model in models:
            if model.content_hash:
                model.last_synced_hash = model.content_hash

        if dry_run:
            await session.rollback()
            print("[dry-run] 已回滚，未写入任何变更")
        else:
            await session.commit()
            print("[done] 已提交")

    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回填复合动作的 content_hash / last_synced_hash"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将变更的条目并回滚，不写库",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = asyncio.run(backfill(dry_run=args.dry_run))
    print(f"[summary] {stats.render()}")


if __name__ == "__main__":
    main()
