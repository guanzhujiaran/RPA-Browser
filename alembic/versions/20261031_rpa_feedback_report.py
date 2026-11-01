"""用户报错 / 反馈表 rpaerrorreport

新增 `rpaerrorreport` 表：用户提交的运行报错 / 反馈（落库 + 弱依赖实时推送服主）。
见 docs/用户提交报错与实时推送服主计划书.md。

Revision ID: 20261031_rpa_feedback_report
Revises: 20261031_wf_notify_channels
Create Date: 2026-10-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261031_rpa_feedback_report'
down_revision: Union[str, Sequence[str], None] = '20261031_wf_notify_channels'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rpaerrorreport',
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_id', sa.String(length=64), nullable=False),
        sa.Column('mid', sa.BIGINT(), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('context', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_rpaerrorreport_created_at'), 'rpaerrorreport', ['created_at'], unique=False)
    op.create_index(op.f('ix_rpaerrorreport_mid'), 'rpaerrorreport', ['mid'], unique=False)
    op.create_index(op.f('ix_rpaerrorreport_report_id'), 'rpaerrorreport', ['report_id'], unique=True)
    op.create_index(op.f('ix_rpaerrorreport_status'), 'rpaerrorreport', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_rpaerrorreport_status'), table_name='rpaerrorreport')
    op.drop_index(op.f('ix_rpaerrorreport_report_id'), table_name='rpaerrorreport')
    op.drop_index(op.f('ix_rpaerrorreport_mid'), table_name='rpaerrorreport')
    op.drop_index(op.f('ix_rpaerrorreport_created_at'), table_name='rpaerrorreport')
    op.drop_table('rpaerrorreport')
