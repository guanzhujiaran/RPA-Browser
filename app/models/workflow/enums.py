"""Workflow 模块 - 筛选/排序枚举 + Fork 同步标志值对象

从 ``app.models/workflow/models.py`` 拆出（原 1141 行单文件）。
历史入口 ``models.py`` 保留为 re-export 门面，既有 import 路径不变。
"""

from __future__ import annotations

from bili_common.models import StrEnumAutoDoc
from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.action_params import VariableSpec
from app.models.execution.condition_models import ConditionRule
from app.models.base.base_sqlmodel import BasePaginationReq
from dataclasses import dataclass
from datetime import datetime
from pydantic import Field
from typing import Dict
from sqlmodel import SQLModel


@dataclass(slots=True)
class UpstreamSyncFlag:
    """Fork 资源的上游同步状态（内部值对象，禁止用裸 Dict 传递）

    由 ActionCrudService / WorkflowCrudService 的 attach_upstream_update_flags
    批量计算，供列表/详情响应使用。详见 docs/fork-上游同步计划书.md。
    """

    has_upstream_update: bool
    upstream_updated_at: int | None  # 秒级时间戳；上游不可用/非 Fork 时为 None
    # 上游版本号与内容指纹（Action 侧填充；Workflow 侧保持默认值）
    upstream_version: str | None = None
    upstream_content_hash: str = ""


class FilterType(StrEnumAutoDoc):
    ALL = "all"
    PRIVATE = "private"
    PUBLIC = "public"
    COMMUNITY = "community"
    VERIFIED = "verified"


class SortBy(StrEnumAutoDoc):
    UPDATED_AT = "updated_at"
    LIKES_COUNT = "likes_count"
    FORKS_COUNT = "forks_count"
    CREATED_AT = "created_at"
    NAME = "name"


class SortOrder(StrEnumAutoDoc):
    DESC = "desc"
    ASC = "asc"

