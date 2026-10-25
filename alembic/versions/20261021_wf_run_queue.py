"""workflow run queue status

给 `workflowrunrecord.status` 增加 `QUEUED`：同浏览器工作流排队时，
排队中的运行以该状态落库，进程重启后据此重新入队
（见 docs/同浏览器工作流排队计划书.md）。

Revision ID: 20261021_wf_run_queue
Revises: 20261021_rpa_baseline
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261021_wf_run_queue"
down_revision: str | Sequence[str] | None = "20261021_rpa_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: SQLAlchemy Enum 存储的是成员名（大写），与基线 DDL 一致
_UPGRADE_ENUM = (
    "ENUM('QUEUED','RUNNING','SUCCESS','FAILED','INTERRUPTED','CANCELLED')"
)
_DOWNGRADE_ENUM = "ENUM('RUNNING','SUCCESS','FAILED','INTERRUPTED','CANCELLED')"


def upgrade() -> None:
    """加入 QUEUED 状态（排队中）。"""
    op.execute(
        "ALTER TABLE workflowrunrecord MODIFY COLUMN status "
        f"{_UPGRADE_ENUM} NOT NULL"
    )


def downgrade() -> None:
    """回滚：排队中的记录收口为中断，再收窄枚举。"""
    op.execute(
        "UPDATE workflowrunrecord SET status='INTERRUPTED' WHERE status='QUEUED'"
    )
    op.execute(
        "ALTER TABLE workflowrunrecord MODIFY COLUMN status "
        f"{_DOWNGRADE_ENUM} NOT NULL"
    )
