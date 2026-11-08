"""userworkflow 增加上次运行可数结果列

Revision ID: 20261109_wf_last_run_counts
Revises: 20261108_wf_run_resource_stats
Create Date: 2026-11-09

背景：工作流卡片不再展示「上次运行：成功/失败」这类结论，改为展示可数结果
——有资源记录时是「成功循环 N 轮」（resource_success），纯步骤工作流是
「成功 N 步」（success_count）。这两个口径随终态事件从运行记录固化到工作流表，
卡片列表无需再 join 运行记录。

存量行填 0：迁移前没有这份快照，界面显示「-」，不做历史反推。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20261109_wf_last_run_counts'
down_revision: str | Sequence[str] | None = '20261108_wf_run_resource_stats'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 与 UserWorkflow 同名字段一一对应
_LAST_RUN_COUNT_COLUMNS: tuple[str, ...] = (
    "last_run_success_count",
    "last_run_total",
    "last_run_resource_total",
    "last_run_resource_success",
)


def upgrade() -> None:
    """Upgrade schema."""
    for name in _LAST_RUN_COUNT_COLUMNS:
        op.add_column(
            "userworkflow",
            sa.Column(
                name,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    """Downgrade schema."""
    for name in reversed(_LAST_RUN_COUNT_COLUMNS):
        op.drop_column("userworkflow", name)
