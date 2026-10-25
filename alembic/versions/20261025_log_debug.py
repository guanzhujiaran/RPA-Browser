"""user-level action log debug mode

给 `userworkflow` / `compositeactionmodel` 增加 `log_debug` 三态列：
用户在工作流 / 复合动作上开启「调试模式」后，该范围强制执行全量日志采集
（入参/结果/变量全记、成功也记、payload 不截断）。替代原服务端 env 开关
`ACTION_LOG_DEBUG_ENABLED`——debug 是用户级设置，不是系统级配置
（见 docs/rpa-workflow-调度外壳计划书.md §9.8）。

Revision ID: 20261025_log_debug
Revises: 20261021_wf_run_queue
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261025_log_debug"
down_revision: str | Sequence[str] | None = "20261021_wf_run_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """两表各加一列可空布尔（None=未设置，与 log_enabled 同款三态语义）。"""
    op.add_column(
        "userworkflow", sa.Column("log_debug", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "compositeactionmodel", sa.Column("log_debug", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("userworkflow", "log_debug")
    op.drop_column("compositeactionmodel", "log_debug")
