"""复合动作增加「版本 + 内容指纹」字段

`compositeactionmodel` 新增 2 列：

- `content_hash`：语义内容（steps / parameters_schema / input_vars / output_vars）
  的 sha256，用于判断"两个动作内容是否等价"，替代原先的 `updated_at` 时间戳比较；
- `last_synced_hash`：Fork 同步快照，记录上次同步时上游的 `content_hash`。

存量数据回填**不在迁移里做**（需要按 Python 逻辑计算哈希，且 Fork 必须在上游
回填后继承），由 `scripts/backfill_action_content_hash.py` 单独执行。
详见 docs/动作版本与内容指纹计划书.md §3 / §8。

`version` 列建表时已存在，本次不新增，只补写入链路。

Revision ID: 20261101_action_content_hash
Revises: 20261031_rpa_feedback_report
Create Date: 2026-11-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261101_action_content_hash'
down_revision: Union[str, Sequence[str], None] = '20261031_rpa_feedback_report'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS: tuple[str, ...] = (
    'content_hash',
    'last_synced_hash',
)


def upgrade() -> None:
    """Upgrade schema."""
    for column in _NEW_COLUMNS:
        # 先带 server_default 才能给存量行补 NOT NULL；回填后去掉默认值，
        # 与模型（无 server_default）保持一致，避免下次 autogenerate 报差异
        op.add_column(
            'compositeactionmodel',
            sa.Column(
                column,
                sa.String(length=64),
                nullable=False,
                server_default=sa.text("''"),
            ),
        )
    for column in _NEW_COLUMNS:
        op.alter_column('compositeactionmodel', column, server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    for column in _NEW_COLUMNS:
        op.drop_column('compositeactionmodel', column)
