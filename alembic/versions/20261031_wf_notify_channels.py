"""工作流运行通知拆成「通道 × 时机」2×2 矩阵

`userworkflow` 新增 4 列：站内 / 站外 × 开始 / 结束
（`notify_inbox_on_start` / `notify_inbox_on_end` /
 `notify_push_on_start` / `notify_push_on_end`）。

存量数据回填：把旧的通道混合开关（`notify_on_start` / `notify_on_end`）复制到两个通道，
保证升级后「该收的通知一条不少、不该收的一条不多」。旧列保留不删——
管理端、导出 JSON、旧客户端仍按「两通道取或」读取（见
docs/通知动作与运行告警计划书.md §E4）。

Revision ID: 20261031_wf_notify_channels
Revises: 20261030_rpa_baseline
Create Date: 2026-10-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261031_wf_notify_channels'
down_revision: Union[str, Sequence[str], None] = '20261030_rpa_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS: tuple[str, ...] = (
    'notify_inbox_on_start',
    'notify_inbox_on_end',
    'notify_push_on_start',
    'notify_push_on_end',
)


def upgrade() -> None:
    """Upgrade schema."""
    for column in _NEW_COLUMNS:
        # 先带 server_default 才能给存量行补 NOT NULL；回填后去掉默认值，
        # 与模型（无 server_default）保持一致，避免下次 autogenerate 报差异
        op.add_column(
            'userworkflow',
            sa.Column(
                column,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('0'),
            ),
        )
    op.execute(
        "UPDATE userworkflow SET "
        "notify_inbox_on_start = notify_on_start, "
        "notify_push_on_start = notify_on_start, "
        "notify_inbox_on_end = notify_on_end, "
        "notify_push_on_end = notify_on_end"
    )
    for column in _NEW_COLUMNS:
        op.alter_column('userworkflow', column, server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    for column in _NEW_COLUMNS:
        op.drop_column('userworkflow', column)
