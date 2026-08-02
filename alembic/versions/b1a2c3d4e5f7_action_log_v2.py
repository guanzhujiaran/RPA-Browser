"""action log v2: move log config onto action base / drop settings table

将「是否采集日志」相关配置直接落到复合操作的基础配置上（compositeactionmodel），
并删除不再需要的 actionlogsetting 表。

Revision ID: b1a2c3d4e5f7
Revises: b1a2c3d4e5f6
Create Date: 2026-07-31 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1a2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = 'b1a2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1) 复合操作基础配置追加日志采集字段
    op.add_column(
        'compositeactionmodel',
        sa.Column('log_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
    )
    op.add_column(
        'compositeactionmodel',
        sa.Column('log_record_params', sa.Boolean(), nullable=False, server_default=sa.text('1')),
    )
    op.add_column(
        'compositeactionmodel',
        sa.Column('log_record_result', sa.Boolean(), nullable=False, server_default=sa.text('1')),
    )
    op.add_column(
        'compositeactionmodel',
        sa.Column('log_record_variables', sa.Boolean(), nullable=False, server_default=sa.text('0')),
    )
    op.add_column(
        'compositeactionmodel',
        sa.Column('log_only_on_error', sa.Boolean(), nullable=False, server_default=sa.text('0')),
    )
    op.add_column(
        'compositeactionmodel',
        sa.Column('log_max_payload_length', sa.Integer(), nullable=False, server_default=sa.text('4000')),
    )
    op.add_column(
        'compositeactionmodel',
        sa.Column('log_retention_days', sa.Integer(), nullable=False, server_default=sa.text('30')),
    )

    # 2) 删除独立的采集配置表（配置已并入 action 基础配置）
    op.drop_index('idx_action_log_setting_unique', table_name='actionlogsetting')
    op.drop_table('actionlogsetting')


def downgrade() -> None:
    """Downgrade schema."""
    # 1) 重建采集配置表
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

    # 2) 移除复合操作上的日志采集字段
    op.drop_column('compositeactionmodel', 'log_retention_days')
    op.drop_column('compositeactionmodel', 'log_max_payload_length')
    op.drop_column('compositeactionmodel', 'log_only_on_error')
    op.drop_column('compositeactionmodel', 'log_record_variables')
    op.drop_column('compositeactionmodel', 'log_record_result')
    op.drop_column('compositeactionmodel', 'log_record_params')
    op.drop_column('compositeactionmodel', 'log_enabled')
