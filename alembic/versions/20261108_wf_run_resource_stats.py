"""workflowrunrecord 增加资源级结果列

Revision ID: 20261108_wf_run_resource_stats
Revises: 20261101_rpa_baseline
Create Date: 2026-11-08

背景：运行记录原本只存步骤口径（total / success_count / failed_count），而运行结论
由 workflowresourcerecord 的资源结果决定，于是出现「18/18 步骤成功」却 status=FAILED、
记录本身无法自解释的情况（2026-11-08 run de801661）。终态落库时把资源四元组与
按 fail_reason 拆出的软失败 / 致命失败一并固化，列表与详情无需再 join 资源表。
见 docs/通知动作与运行告警计划书.md §D5.1。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20261108_wf_run_resource_stats'
down_revision: str | Sequence[str] | None = '20261101_rpa_baseline'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 与 WorkflowRunRecord / WorkflowRunSummary 的同名字段一一对应
_RESOURCE_COLUMNS: tuple[str, ...] = (
    "resource_total",
    "resource_success",
    "resource_failed",
    "resource_skipped",
    "resource_soft_failed",
    "resource_fatal_failed",
)


def upgrade() -> None:
    """Upgrade schema."""
    # 存量行填 0：历史运行没有资源快照，不能伪造成「0 成功 0 失败」之外的值。
    for name in _RESOURCE_COLUMNS:
        op.add_column(
            "workflowrunrecord",
            sa.Column(
                name,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    """Downgrade schema."""
    for name in reversed(_RESOURCE_COLUMNS):
        op.drop_column("workflowrunrecord", name)
