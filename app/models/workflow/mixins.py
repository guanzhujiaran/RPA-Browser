"""Workflow 模块可复用字段混入

从 ``models.py`` 拆出。原文件中「上游同步 5 字段」在
``WorkflowDetailResponse`` / ``WorkflowListItemResponse`` /
``CompositeActionDetailResponse`` / ``CompositeActionListItemResponse``
四个响应类里逐字复制 4 份，此处收敛为单一基类。
"""

from __future__ import annotations

from sqlmodel import SQLModel


class UpstreamSyncFields(SQLModel):
    """Fork 资源的上游同步状态字段（响应类混入）

    由 ActionCrudService / WorkflowCrudService 的 attach_upstream_update_flags
    批量计算后填入。详见 docs/fork-上游同步计划书.md。
    """

    has_upstream_update: bool = False
    upstream_updated_at: int | None = None
    last_synced_at: int | None = None
    auto_sync_upstream: bool = False
    local_steps_modified: bool = False
