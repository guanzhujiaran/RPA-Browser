"""报错反馈支持游客提交 + 管理端处理字段

Revision ID: 20261115_feedback_guest
Revises: 20261109_wf_last_run_counts
Create Date: 2026-11-15

背景：反馈入口放开给未登录游客（「出错了但还没登录」是最常见的求助场景），
同时管理后台需要对全部反馈做 CRUD 与状态处理。故：

- ``rpaerrorreport.mid`` 改为可空（游客提交无 mid）；
- 新增 ``uname``（提交者昵称快照）与 ``contact``（联系方式，游客填写）；
- 新增 ``admin_remark`` / ``handled_by`` / ``handled_at``（管理端处理留痕）。

存量数据均来自登录用户，``mid`` 非空不受影响；新列全部可空 / 空串默认。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261115_feedback_guest"
down_revision: str | Sequence[str] | None = "20261109_wf_last_run_counts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "rpaerrorreport",
        "mid",
        existing_type=sa.BIGINT(),
        nullable=True,
    )
    op.add_column(
        "rpaerrorreport",
        sa.Column("uname", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "rpaerrorreport",
        sa.Column("contact", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "rpaerrorreport",
        sa.Column("admin_remark", sa.Text(), nullable=True),
    )
    op.add_column(
        "rpaerrorreport",
        sa.Column("handled_by", sa.BIGINT(), nullable=True),
    )
    op.add_column(
        "rpaerrorreport",
        sa.Column("handled_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("rpaerrorreport", "handled_at")
    op.drop_column("rpaerrorreport", "handled_by")
    op.drop_column("rpaerrorreport", "admin_remark")
    op.drop_column("rpaerrorreport", "contact")
    op.drop_column("rpaerrorreport", "uname")
    # 回退后 mid 必须非空：先把游客反馈（mid 为空）清理掉，避免迁移失败
    op.execute("DELETE FROM rpaerrorreport WHERE mid IS NULL")
    op.alter_column(
        "rpaerrorreport",
        "mid",
        existing_type=sa.BIGINT(),
        nullable=False,
    )
