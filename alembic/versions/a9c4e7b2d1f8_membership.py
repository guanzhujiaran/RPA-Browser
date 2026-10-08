"""membership: 时长账户/流水/月卡/签到/兑换码/使用统计

Revision ID: a9c4e7b2d1f8
Revises: c710c4a3f4b6
Create Date: 2026-10-02

手动迁移脚本（对应 docs/浏览器使用时长与会员权益计划书.md §6）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = "a9c4e7b2d1f8"
down_revision: Union[str, Sequence[str], None] = "c710c4a3f4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # userdurationaccount 用户时长账户
    op.create_table(
        "userdurationaccount",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mid", sa.BIGINT(), nullable=False),
        sa.Column("balance_seconds", sa.Integer(), nullable=False),
        sa.Column("total_granted_seconds", sa.Integer(), nullable=False),
        sa.Column("total_consumed_seconds", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mid", name="uq_duration_account_mid"),
    )
    op.create_index(
        op.f("ix_userdurationaccount_created_at"),
        "userdurationaccount",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_userdurationaccount_mid"), "userdurationaccount", ["mid"], unique=False
    )

    # durationledger 时长变动流水
    op.create_table(
        "durationledger",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mid", sa.BIGINT(), nullable=False),
        sa.Column("change_seconds", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column(
            "change_type",
            sa.Enum(
                "sign_in",
                "activity",
                "redeem",
                "consume",
                "adjust",
                name="ledgerchangetype",
            ),
            nullable=False,
        ),
        sa.Column(
            "ref_id", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False
        ),
        sa.Column(
            "workflow_id", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True
        ),
        sa.Column("run_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("browser_id", sa.BIGINT(), nullable=True),
        sa.Column(
            "remark", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_duration_ledger_mid_created",
        "durationledger",
        ["mid", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_durationledger_created_at"),
        "durationledger",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_durationledger_mid"), "durationledger", ["mid"], unique=False
    )
    op.create_index(
        op.f("ix_durationledger_change_type"),
        "durationledger",
        ["change_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_durationledger_workflow_id"),
        "durationledger",
        ["workflow_id"],
        unique=False,
    )

    # monthcardrecord 月卡记录
    op.create_table(
        "monthcardrecord",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mid", sa.BIGINT(), nullable=False),
        sa.Column(
            "source",
            sa.Enum("redeem", "activity", "admin", name="monthcardsourceenum"),
            nullable=False,
        ),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("expire_at", sa.DateTime(), nullable=False),
        sa.Column(
            "ref_id", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False
        ),
        sa.Column(
            "remark", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_month_card_mid_expire",
        "monthcardrecord",
        ["mid", "expire_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monthcardrecord_created_at"),
        "monthcardrecord",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monthcardrecord_mid"), "monthcardrecord", ["mid"], unique=False
    )

    # signinrecord 每日签到记录
    op.create_table(
        "signinrecord",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mid", sa.BIGINT(), nullable=False),
        sa.Column("sign_date", sa.Date(), nullable=False),
        sa.Column("reward_seconds", sa.Integer(), nullable=False),
        sa.Column("continuous_days", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mid", "sign_date", name="uq_signin_mid_date"),
    )
    op.create_index(
        op.f("ix_signinrecord_created_at"), "signinrecord", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_signinrecord_mid"), "signinrecord", ["mid"], unique=False)

    # redemptioncode 兑换码
    op.create_table(
        "redemptioncode",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("code", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column(
            "code_type",
            sa.Enum("duration", "month_card", name="redeemcodetypeenum"),
            nullable=False,
        ),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("card_days", sa.Integer(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("expire_at", sa.DateTime(), nullable=True),
        sa.Column(
            "batch_no", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column(
            "remark", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False
        ),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index(
        op.f("ix_redemptioncode_created_at"),
        "redemptioncode",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_redemptioncode_batch_no"), "redemptioncode", ["batch_no"], unique=False
    )

    # coderedemptionrecord 兑换流水
    op.create_table(
        "coderedemptionrecord",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("mid", sa.BIGINT(), nullable=False),
        sa.Column(
            "reward_summary",
            sqlmodel.sql.sqltypes.AutoString(length=200),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "mid", name="uq_redeem_code_mid"),
    )
    op.create_index(
        op.f("ix_coderedemptionrecord_created_at"),
        "coderedemptionrecord",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_coderedemptionrecord_code"),
        "coderedemptionrecord",
        ["code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_coderedemptionrecord_mid"),
        "coderedemptionrecord",
        ["mid"],
        unique=False,
    )

    # browserusagedailystat 使用时长日统计
    op.create_table(
        "browserusagedailystat",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mid", sa.BIGINT(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("browser_id", sa.BIGINT(), nullable=False),
        sa.Column("workflow_seconds", sa.Integer(), nullable=False),
        sa.Column("manual_seconds", sa.Integer(), nullable=False),
        sa.Column("workflow_run_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mid", "stat_date", "browser_id", name="uq_usage_stat_mid_date_browser"
        ),
    )
    op.create_index(
        op.f("ix_browserusagedailystat_created_at"),
        "browserusagedailystat",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browserusagedailystat_mid"),
        "browserusagedailystat",
        ["mid"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browserusagedailystat_stat_date"),
        "browserusagedailystat",
        ["stat_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("browserusagedailystat")
    op.drop_table("coderedemptionrecord")
    op.drop_table("redemptioncode")
    op.drop_table("signinrecord")
    op.drop_table("monthcardrecord")
    op.drop_table("durationledger")
    op.drop_table("userdurationaccount")
    sa.Enum(name="ledgerchangetype").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="monthcardsourceenum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="redeemcodetypeenum").drop(op.get_bind(), checkfirst=True)
