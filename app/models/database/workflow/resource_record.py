"""工作流主资源处理记录（通用去重 / 断点续跑）

不绑定具体业务：抽奖 / 预约抽奖 / 视频 / 动态等任何「工作流批量处理一批带主 id
的数据」的场景，都用 resource_type 区分命名空间、resource_id 存业务主键。

去重作用域（按工作流 + 浏览器隔离）：
    UNIQUE(mid, workflow_id, browser_id, resource_type, resource_id)
    - 同一用户的同一工作流在同一浏览器上，对同一资源只保留一条记录；
    - 不同工作流互不影响（A 工作流处理过不影响 B 工作流）；
    - 不同浏览器互不影响（工作流绑定浏览器执行，各浏览器登录态/账号独立）；
    - 不同用户互不影响；
    - fork 出的新工作流 workflow_id 不同，默认不继承历史。

去重口径：仅 status='success' 参与去重；failed 下次仍会被捞起重试（断点续跑）。

约定：
    - mid 沿用 CommunityResourceBase 的 str 存储（工作流模块统一用 str(max_length=255)）；
    - resource_id 用字符串存，兼容 int 主键 / 复合 id / 第三方字符串 id；
    - created_at / updated_at 由 BaseSQLModel 统一提供（updated_at 自动 onupdate）。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field

from bili_common.models import StrEnumAutoDoc
from app.models.base.base_sqlmodel import BaseSQLModel


class ResourceProcessStatus(StrEnumAutoDoc):
    """资源项处理状态（去重只认 SUCCESS）"""

    SUCCESS = "success"  # 处理成功：后续执行默认跳过
    FAILED = "failed"  # 处理失败：下次仍会被捞起重试
    SKIPPED = "skipped"  # 显式跳过（不参与去重，仅做标记）


class WorkflowResourceRecord(BaseSQLModel, table=True):
    """工作流主资源处理记录表

    一条记录 = 某用户的某工作流对某个资源项的最近一次处理结果。
    重复处理走 upsert（依赖唯一约束幂等），process_count 累加。
    """

    __table_args__ = (
        # 幂等核心：按工作流 + 浏览器隔离
        UniqueConstraint(
            "mid",
            "workflow_id",
            "browser_id",
            "resource_type",
            "resource_id",
            name="uq_wf_resource_scope",
        ),
        # 去重查询主路径：取某工作流某浏览器某类型下已成功的 id 集合
        Index(
            "idx_wf_resource_dedupe",
            "mid",
            "workflow_id",
            "browser_id",
            "resource_type",
            "status",
        ),
        # 历史展示 / 清理：按工作流 + 最近处理时间
        Index(
            "idx_wf_resource_run_time",
            "workflow_id",
            "updated_at",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    mid: str = Field(max_length=255, index=True, description="归属用户ID")
    workflow_id: str = Field(
        max_length=100, index=True, description="关联工作流ID（UserWorkflow.workflow_id）"
    )
    browser_id: str = Field(
        max_length=100,
        default="",
        index=True,
        description="执行浏览器ID（工作流绑定浏览器，去重按浏览器隔离）",
    )
    run_id: str | None = Field(
        default=None,
        max_length=64,
        index=True,
        description="最近一次处理该资源的工作流运行ID（WorkflowRunRecord.run_id）",
    )
    resource_type: str = Field(
        max_length=64,
        description="资源类型命名空间（如 lottery/reserve_lottery/video，可自定义，不做外键）",
    )
    resource_id: str = Field(
        max_length=128, description="资源业务主ID（字符串存储，兼容各类主键）"
    )
    status: ResourceProcessStatus = Field(
        default=ResourceProcessStatus.FAILED,
        index=True,
        description="处理状态：success/failed/skipped，仅 success 参与去重",
    )
    fail_reason: str | None = Field(
        default=None, max_length=500, description="最近一次失败原因（成功时为空）"
    )
    process_count: int = Field(
        default=1, description="累计处理次数（重复 upsert 时累加）"
    )
    extra: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="预留：资源快照等附加信息（标题/跳转等，便于前端展示）",
    )
    first_processed_at: datetime = Field(
        default_factory=datetime.now, description="首次处理时间"
    )
    last_processed_at: datetime = Field(
        default_factory=datetime.now, index=True, description="最近一次处理时间"
    )
