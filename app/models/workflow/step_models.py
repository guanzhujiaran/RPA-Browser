"""Workflow 模块 - 工作流步骤请求/响应模型

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
from sqlmodel import SQLModel


class WorkflowStepRequest(SQLModel):
    """
    工作流步骤请求 - 与 WorkflowStep 模型对齐，与 BaseAction 创建参数对齐
    定义工作流中单个步骤的配置。
    """

    action_id: int | str = Field(
        description="操作ID：内置动作为名字符串(如 click/input/llm)，复合动作为雪花 int"
    )
    action_type: BuiltinActionType | str | None = Field(
        default=None, description="操作类型"
    )
    mid: int | None = Field(default=None, description="用户ID")
    params: dict = Field(
        default_factory=dict, description="操作参数，支持 {{变量名}} 模板替换"
    )
    retry: int = Field(default=0, description="失败重试次数")
    condition: ConditionRule | None = Field(
        default=None, description="执行条件规则（结构化条件，不使用 eval）"
    )
    output_var: str | None = Field(default=None, description="结果变量键名")
    input_vars: dict = Field(default_factory=dict, description="输入变量")
    output_vars: list[str] = Field(default_factory=list, description="输出变量名称列表")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    children: list["WorkflowStepRequest"] | None = Field(
        default=None, description="子步骤列表（用于循环体或分支）"
    )
    loop_count: int | None = Field(default=None, description="固定循环次数")
    loop_while: str | None = Field(
        default=None, description="条件循环，表达式为true时继续"
    )
    loop_until: str | None = Field(
        default=None, description="条件退出，表达式为true时退出"
    )


class WorkflowStepResponse(SQLModel):
    """工作流步骤响应 - 与 WorkflowStep 模型对齐，与 BaseAction 创建参数对齐"""

    action_id: int | str = Field(
        description="操作ID：内置动作为名字符串，复合动作为雪花 int"
    )
    action_type: BuiltinActionType | str | None = Field(
        default=None, description="操作类型"
    )
    mid: int | None = Field(default=None, description="用户ID")
    params: dict = Field(description="操作参数")
    retry: int = Field(default=0, description="失败重试次数")
    condition: ConditionRule | None = Field(default=None, description="执行条件规则")
    output_var: str | None = Field(default=None, description="结果变量键名")
    input_vars: dict = Field(default_factory=dict, description="输入变量")
    output_vars: list[str] = Field(default_factory=list, description="输出变量名称列表")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    children: list["WorkflowStepResponse"] | None = Field(
        default=None, description="子步骤列表"
    )
    loop_count: int | None = Field(default=None, description="固定循环次数")
    loop_while: str | None = Field(default=None, description="条件循环")
    loop_until: str | None = Field(default=None, description="条件退出")

