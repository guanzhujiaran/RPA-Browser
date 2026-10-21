"""Workflow 模块 - 系统级模型（动作参数元数据/重载）

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
from typing import Any
from sqlmodel import SQLModel


class ActionParameterResponse(SQLModel):
    """操作参数响应"""

    name: str
    type: str
    required: bool
    default: Any | None = None
    description: str = ""
    # SQLModel 验证规则（根据类型设置，数值类型用 min/max，字符串类型用 min_length/max_length）
    min: float | None = Field(
        default=None, description="最小值（仅数值类型：int/float），字符串类型为 None"
    )
    max: float | None = Field(
        default=None, description="最大值（仅数值类型：int/float），字符串类型为 None"
    )
    min_length: int | None = Field(
        default=None, description="最小长度（仅字符串类型：str），数值类型为 None"
    )
    max_length: int | None = Field(
        default=None, description="最大长度（仅字符串类型：str），数值类型为 None"
    )
    enum: list[Any] | None = Field(
        default=None, description="枚举值列表，无枚举时为 None"
    )
    format: str | None = Field(
        default=None, description="格式要求（如 email, uri 等），无格式要求时为 None"
    )


class ReloadActionsResponse(SQLModel):
    """重新加载响应"""

    loaded: int

