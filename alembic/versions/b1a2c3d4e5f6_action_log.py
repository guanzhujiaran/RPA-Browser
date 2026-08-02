"""action log

Revision ID: b1a2c3d4e5f6
Revises: a3e376e46d4e
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b1a2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'a3e376e46d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'actionlogsetting',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mid', sa.String(length=255), nullable=False),
        sa.Column('action_id', sa.String(length=100), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('record_params', sa.Boolean(), nullable=False),
        sa.Column('record_result', sa.Boolean(), nullable=False),
        sa.Column('record_variables', sa.Boolean(), nullable=False),
        sa.Column('only_on_error', sa.Boolean(), nullable=False),
        sa.Column('max_payload_length', sa.Integer(), nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('remark', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_action_log_setting_unique', 'actionlogsetting',
        ['mid', 'action_id'], unique=True,
    )

    op.create_table(
        'actionlogrecord',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('log_id', sa.String(length=64), nullable=False),
        sa.Column('mid', sa.String(length=255), nullable=False),
        sa.Column('execution_id', sa.String(length=64), nullable=False),
        sa.Column('parent_execution_id', sa.String(length=64), nullable=True),
        sa.Column('depth', sa.Integer(), nullable=False),
        sa.Column('action_id', sa.String(length=100), nullable=False),
        sa.Column('action_name', sa.String(length=200), nullable=False),
        sa.Column('action_type', sa.String(length=64), nullable=False),
        sa.Column('source', sa.String(length=8), nullable=False),
        sa.Column('workflow_id', sa.String(length=100), nullable=True),
        sa.Column('browser_id', sa.String(length=100), nullable=False),
        sa.Column('session_id', sa.String(length=100), nullable=False),
        sa.Column('page_url', sa.String(length=1000), nullable=False),
        sa.Column('status', sa.String(length=7), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('params', sa.JSON(), nullable=True),
        sa.Column('result_data', sa.JSON(), nullable=True),
        sa.Column('variables', sa.JSON(), nullable=True),
        sa.Column('logs', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.String(length=2000), nullable=True),
        sa.Column('execution_time', sa.Float(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('log_id'),
    )
    op.create_index(
        'idx_action_log_mid_started', 'actionlogrecord',
        ['mid', 'started_at'], unique=False,
    )
    op.create_index(
        'idx_action_log_execution_order', 'actionlogrecord',
        ['execution_id', 'id'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_action_log_execution_order', table_name='actionlogrecord')
    op.drop_index('idx_action_log_mid_started', table_name='actionlogrecord')
    op.drop_index('ix_actionlogrecord_log_id', table_name='actionlogrecord')
    op.drop_table('actionlogrecord')

    op.drop_index('idx_action_log_setting_unique', table_name='actionlogsetting')
    op.drop_table('actionlogsetting')
