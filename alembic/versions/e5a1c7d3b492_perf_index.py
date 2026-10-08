"""perf_index: 复合索引（计费心跳扫描 / 使用统计区间查询 / 跨进程并发防护）

对应代码：
    - app/models/database/workflow/models.py: WorkflowRunRecord
    - app/models/database/membership/models.py: BrowserUsageDailyStat

背景（详见计划书 §14.2 / §14.4）：
    - 计费心跳按「status=RUNNING 且 trigger_source=SCHEDULE 且 settled_seconds IS NULL」
      扫描，原仅有 status 单列索引，大表下半表扫描；
    - runner 前置校验需按 (browser_id, status) 判断同浏览器是否已有运行中的工作流；
    - 使用日统计按 (mid, stat_date 范围) 查询，需要复合索引支撑范围过滤。

Revision ID: e5a1c7d3b492
Revises: c4e7a9b2f5d8
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5a1c7d3b492"
down_revision: Union[str, Sequence[str], None] = "c4e7a9b2f5d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 计费心跳扫描：RUNNING + SCHEDULE（计划书 §8.1 / §14.2）
    op.create_index(
        "idx_workflow_run_status_trigger",
        "workflowrunrecord",
        ["status", "trigger_source"],
        unique=False,
    )
    # 跨进程并发防护：同一浏览器是否已有运行中的工作流（计划书 §14.4）
    op.create_index(
        "idx_workflow_run_browser_status",
        "workflowrunrecord",
        ["browser_id", "status"],
        unique=False,
    )
    # 使用日统计区间查询：mid + stat_date 范围（计划书 §14.6）
    op.create_index(
        "idx_usage_stat_mid_date",
        "browserusagedailystat",
        ["mid", "stat_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_usage_stat_mid_date", table_name="browserusagedailystat")
    op.drop_index("idx_workflow_run_browser_status", table_name="workflowrunrecord")
    op.drop_index("idx_workflow_run_status_trigger", table_name="workflowrunrecord")
