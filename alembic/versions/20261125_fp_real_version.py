"""userbrowserinfo 增加真实浏览器版本列

Revision ID: 20261125_fp_real_version
Revises: 20261115_feedback_guest
Create Date: 2026-11-25

背景：浏览器版本此前只有「模拟版本」``fingerprint_brand_version``（下发给
``--fingerprint-brand-version``），真实二进制版本仅存在于
``app/utils/consts/browser_exe_info/browser_exec_info.json``，启动时靠 UA 全等反查，
查不到会静默回落第一条。见 docs/浏览器真实版本与模拟版本一致性计划书.md。

本次：

- 新增 ``fingerprint_real_version``（真实版本，来自清单 ``full_version``，选二进制用）；
- 历史行按 ``patchright_browser_ua`` 回填真实版本，并把 ``fingerprint_brand_version``
  修正为清单里的 ``full_version``（旧值是 browserforge 生成的 patch 号，如 139.0.7258.139，
  与清单 139.0.7258.154 不一致）。

回填映射取自迁移编写时的清单快照，迁移一旦发布不再随 JSON 变化。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261125_fp_real_version"
down_revision: str | Sequence[str] | None = "20261115_feedback_guest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (browser_ua, full_version) —— 与编写迁移时的 browser_exec_info.json 一致
_UA_TO_FULL_VERSION: tuple[tuple[str, str], ...] = (
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
        "139.0.7258.154",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "142.0.7444.175",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "144.0.7559.132",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "148.0.7778.215",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "150.0.7871.186",
    ),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "userbrowserinfo",
        sa.Column("fingerprint_real_version", sa.String(length=64), nullable=True),
    )

    table = sa.table(
        "userbrowserinfo",
        sa.column("patchright_browser_ua", sa.String()),
        sa.column("fingerprint_brand_version", sa.String()),
        sa.column("fingerprint_real_version", sa.String()),
    )
    for browser_ua, full_version in _UA_TO_FULL_VERSION:
        op.execute(
            table.update()
            .where(table.c.patchright_browser_ua == browser_ua)
            .values(
                fingerprint_real_version=full_version,
                fingerprint_brand_version=full_version,
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("userbrowserinfo", "fingerprint_real_version")
