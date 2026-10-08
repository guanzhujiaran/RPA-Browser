"""payment: 支付商品映射与入账记录（Casdoor 收银台对账，计划书 §3.3）

Revision ID: d7e2a9b4c6f1
Revises: c3f8b6a1e9d2
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = "d7e2a9b4c6f1"
down_revision: Union[str, Sequence[str], None] = "c3f8b6a1e9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # paymentorder 支付入账记录（幂等键 payment_name）
    # 注：paymentproduct 映射表已随方案A（商品名约定解析）废弃，不再创建；
    #     若旧版本曾建过该表，留存无碍（代码不再读写），可手动 DROP。
    op.create_table(
        "paymentorder",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "payment_name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False
        ),
        sa.Column(
            "casdoor_user", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False
        ),
        sa.Column("mid", sa.BIGINT(), nullable=False),
        sa.Column(
            "product_name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False
        ),
        sa.Column(
            "grant_type",
            sa.Enum("duration", "month_card", name="paymentgranttypeenum"),
            nullable=False,
        ),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("card_days", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_name", name="uq_payment_order_name"),
    )
    op.create_index(
        "idx_payment_order_mid_created",
        "paymentorder",
        ["mid", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_paymentorder_created_at"), "paymentorder", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_paymentorder_mid"), "paymentorder", ["mid"], unique=False)
    op.create_index(
        op.f("ix_paymentorder_payment_name"),
        "paymentorder",
        ["payment_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("paymentorder")
    sa.Enum(name="paymentgranttypeenum").drop(op.get_bind(), checkfirst=True)
