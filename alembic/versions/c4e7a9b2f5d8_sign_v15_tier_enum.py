"""sign_v15: 档位枚举值 basic/advanced/peak -> 7d/14d/28d（计划书 §13.6）

Revision ID: c4e7a9b2f5d8
Revises: b2f6c8d1e4a7
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4e7a9b2f5d8"
down_revision: Union[str, Sequence[str], None] = "b2f6c8d1e4a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 历史数据已随 v1.4 自测清理，无 basic/advanced/peak 存量行；仍做一次兜底转换
    op.execute("UPDATE signrewardexchange SET tier = '7d' WHERE tier = 'basic'")
    op.execute("UPDATE signrewardexchange SET tier = '14d' WHERE tier = 'advanced'")
    op.execute("UPDATE signrewardexchange SET tier = '28d' WHERE tier = 'peak'")
    op.alter_column(
        "signrewardexchange",
        "tier",
        existing_type=sa.Enum("7d", "14d", "28d", name="signrewardtierenum"),
        type_=sa.Enum("7d", "14d", "28d", name="signrewardtierenum"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "signrewardexchange",
        "tier",
        existing_type=sa.Enum("basic", "advanced", "peak", name="signrewardtierenum"),
        type_=sa.Enum("basic", "advanced", "peak", name="signrewardtierenum"),
        existing_nullable=False,
    )
