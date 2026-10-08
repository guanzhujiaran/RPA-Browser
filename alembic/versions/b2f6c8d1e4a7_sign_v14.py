"""sign_v14: 签到玩法改版（累计天数档案 + 奖励档位兑换 + 补签标记，计划书 §12）

Revision ID: b2f6c8d1e4a7
Revises: d7e2a9b4c6f1
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "b2f6c8d1e4a7"
down_revision: Union[str, Sequence[str], None] = "d7e2a9b4c6f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # signinrecord 增列：补签标记 + 本次消耗的补登卡数
    op.add_column(
        "signinrecord",
        sa.Column(
            "is_makeup", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "signinrecord",
        sa.Column(
            "makeup_card_cost",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # usersignprofile 用户签到档案（累计天数 + 补登卡持有量）
    op.create_table(
        "usersignprofile",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mid", sa.BIGINT(), nullable=False),
        sa.Column("total_sign_days", sa.Integer(), nullable=False),
        sa.Column("makeup_cards", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mid", name="uq_user_sign_profile_mid"),
    )
    op.create_index(
        op.f("ix_usersignprofile_created_at"),
        "usersignprofile",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_usersignprofile_mid"), "usersignprofile", ["mid"], unique=False
    )

    # signrewardexchange 里程碑奖励档位兑换记录（每档每用户仅一次）
    op.create_table(
        "signrewardexchange",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mid", sa.BIGINT(), nullable=False),
        sa.Column(
            "tier",
            sa.Enum("basic", "advanced", "peak", name="signrewardtierenum"),
            nullable=False,
        ),
        sa.Column("reward_seconds", sa.Integer(), nullable=False),
        sa.Column("makeup_cards", sa.Integer(), nullable=False),
        sa.Column("total_sign_days", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mid", "tier", name="uq_sign_reward_exchange_mid_tier"),
    )
    op.create_index(
        op.f("ix_signrewardexchange_created_at"),
        "signrewardexchange",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_signrewardexchange_mid"), "signrewardexchange", ["mid"], unique=False
    )
    op.create_index(
        op.f("ix_signrewardexchange_tier"), "signrewardexchange", ["tier"], unique=False
    )


def downgrade() -> None:
    op.drop_table("signrewardexchange")
    sa.Enum(name="signrewardtierenum").drop(op.get_bind(), checkfirst=True)
    op.drop_table("usersignprofile")
    op.drop_column("signinrecord", "makeup_card_cost")
    op.drop_column("signinrecord", "is_makeup")
