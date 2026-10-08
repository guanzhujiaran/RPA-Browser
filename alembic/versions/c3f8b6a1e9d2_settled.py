"""workflowrunrecord: 新增 settled_seconds 结算幂等标记

Revision ID: c3f8b6a1e9d2
Revises: a9c4e7b2d1f8
Create Date: 2026-10-02

见 docs/浏览器使用时长与会员权益计划书.md §8（心跳预扣 / 熔断 / 崩溃兜底结算）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c3f8b6a1e9d2"
down_revision: Union[str, Sequence[str], None] = "a9c4e7b2d1f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflowrunrecord",
        sa.Column(
            "settled_seconds",
            sa.Integer(),
            nullable=True,
            comment="时长结算标记（秒）：None=未结算",
        ),
    )


def downgrade() -> None:
    op.drop_column("workflowrunrecord", "settled_seconds")
