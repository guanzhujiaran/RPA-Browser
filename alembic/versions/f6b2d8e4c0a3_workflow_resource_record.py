"""workflow_resource_record: 工作流主资源处理记录（通用去重 / 断点续跑，按工作流隔离）

对应代码：
    - app/models/database/workflow/resource_record.py: WorkflowResourceRecord

设计要点：
    - 不绑定具体业务，resource_type 为命名空间、resource_id 存业务主键（字符串）；
    - UNIQUE(mid, workflow_id, browser_id, resource_type, resource_id) 为幂等核心，
      去重按工作流 + 浏览器隔离（工作流绑定浏览器执行，各浏览器登录态独立）；
    - 仅 status='success' 参与去重，failed 下次重试。

Revision ID: f6b2d8e4c0a3
Revises: e5a1c7d3b492
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6b2d8e4c0a3"
down_revision: Union[str, Sequence[str], None] = "e5a1c7d3b492"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflowresourcerecord",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "mid", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False
        ),
        sa.Column(
            "workflow_id",
            sqlmodel.sql.sqltypes.AutoString(length=100),
            nullable=False,
        ),
        sa.Column(
            "browser_id",
            sqlmodel.sql.sqltypes.AutoString(length=100),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "run_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True
        ),
        sa.Column(
            "resource_type",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "resource_id",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=False,
        ),
        # 枚举以 value 存取（success/failed/skipped），与 enum_value_type 约定一致
        sa.Column(
            "status",
            sa.Enum(
                "success",
                "failed",
                "skipped",
                name="resourceprocessstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "fail_reason",
            sqlmodel.sql.sqltypes.AutoString(length=500),
            nullable=True,
        ),
        sa.Column("process_count", sa.Integer(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("first_processed_at", sa.DateTime(), nullable=False),
        sa.Column("last_processed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mid",
            "workflow_id",
            "browser_id",
            "resource_type",
            "resource_id",
            name="uq_wf_resource_scope",
        ),
    )
    # 去重查询主路径
    op.create_index(
        "idx_wf_resource_dedupe",
        "workflowresourcerecord",
        ["mid", "workflow_id", "browser_id", "resource_type", "status"],
        unique=False,
    )
    # 历史展示 / 清理
    op.create_index(
        "idx_wf_resource_run_time",
        "workflowresourcerecord",
        ["workflow_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflowresourcerecord_mid"),
        "workflowresourcerecord",
        ["mid"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflowresourcerecord_workflow_id"),
        "workflowresourcerecord",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflowresourcerecord_browser_id"),
        "workflowresourcerecord",
        ["browser_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflowresourcerecord_run_id"),
        "workflowresourcerecord",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflowresourcerecord_status"),
        "workflowresourcerecord",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflowresourcerecord_last_processed_at"),
        "workflowresourcerecord",
        ["last_processed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflowresourcerecord_created_at"),
        "workflowresourcerecord",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("workflowresourcerecord")
    sa.Enum(name="resourceprocessstatus").drop(op.get_bind(), checkfirst=True)
