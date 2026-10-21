"""Workflow 模块 - 操作执行/预览/验证/单步执行模型

从 ``app.models/workflow/models.py`` 拆出（原 1141 行单文件）。
历史入口 ``models.py`` 保留为 re-export 门面，既有 import 路径不变。
"""

from __future__ import annotations

from bili_common.models import StrEnumAutoDoc
from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.action_params import (
    VariableSpec,
    is_composite_action_id,
)
from app.models.execution.condition_models import ConditionRule
from app.models.base.base_sqlmodel import BasePaginationReq
from dataclasses import dataclass
from datetime import datetime
from pydantic import Field
from typing import Any, Dict
from sqlmodel import SQLModel


from app.models.workflow.step_models import WorkflowStepRequest


class ActionExecuteRequest(SQLModel):
    """执行操作请求"""

    action_id: int | str = Field(
        description="操作ID：内置动作为名字符串，复合动作为雪花 int"
    )
    params: dict = Field(default_factory=dict, description="操作参数")
    variables: dict = Field(default_factory=dict, description="变量池")
    input_vars: dict = Field(
        default_factory=dict, description="输入变量，会被合并到 variables 变量池中"
    )
    output_vars: list[str] = Field(default_factory=list, description="输出变量名称列表")
    page_index: int | None = Field(
        default=None, description="页面索引，指定在哪个 tab 页执行操作"
    )

    @property
    def action_type(self) -> BuiltinActionType:
        if not is_composite_action_id(self.action_id) and (
            self.action_id in BuiltinActionType
        ):
            return BuiltinActionType(self.action_id)
        return BuiltinActionType.COMPOSITE  # 自定义操作默认为复合操作


class ActionPreviewRequest(SQLModel):
    """预览参数替换请求"""

    action_id: int | str = Field(
        description="操作ID：内置动作为名字符串，复合动作为雪花 int"
    )
    params: dict = Field(default_factory=dict, description="参数")
    input_vars: dict = Field(
        default_factory=dict, description="输入变量，会被合并到预览变量池中"
    )


class ActionValidateRequest(SQLModel):
    """验证参数请求"""

    action_id: int | str = Field(
        description="操作ID：内置动作为名字符串，复合动作为雪花 int"
    )
    params: dict = Field(default_factory=dict, description="待验证参数")
    input_vars: dict = Field(
        default_factory=dict, description="输入变量，会被合并到变量池中"
    )


class ExecuteStepRequest(SQLModel):
    """单步执行请求"""

    action_id: int | str = Field(
        description="操作ID：内置动作为名字符串，复合动作为雪花 int"
    )
    params: dict = Field(default_factory=dict, description="操作参数")
    variables: dict = Field(default_factory=dict, description="变量池")
    step_index: int = Field(default=0, description="步骤索引")
    page_index: int | None = Field(
        default=None, description="页面索引，指定在哪个 tab 页执行操作"
    )


class ActionResultResponse(SQLModel):
    """操作执行结果"""

    success: bool
    data: Any = None
    error: str | None = None
    execution_time: float = 0.0
    action_id: int | str = ""
    action_name: str = ""
    variables: dict = Field(default_factory=dict, description="执行后的全局变量")
    replaced_params: dict = Field(
        default_factory=dict, description="变量替换后的实际调用参数"
    )


class StepPreviewItem(SQLModel):
    """步骤预览项 — 支持递归展开控制流分支"""

    step_index: int
    action_id: int | str
    original_params: Dict
    replaced_params: Dict
    input_vars: dict = Field(default_factory=dict, description="输入变量")
    output_vars: list[str] = Field(default_factory=list, description="输出变量名称列表")
    preview_variables: dict = Field(
        default_factory=dict, description="该步骤模拟后的变量"
    )
    branches: dict | None = Field(
        default=None, description="if-else 分支配对 {true: [...], false: [...]}"
    )
    loop_preview: list | None = Field(default=None, description="循环体预览步骤列表")
    children: list[dict] | None = Field(
        default=None, description="复合动作子步骤预览列表"
    )


class ActionPreviewResponse(SQLModel):
    """预览响应"""

    action_id: int | str
    action_name: str
    is_composite: bool
    steps_preview: list[StepPreviewItem]
    replaced_params: Dict
    found_params: list[str]
    preview_result: dict = Field(default_factory=dict, description="模拟执行结果数据")
    preview_variables: dict = Field(
        default_factory=dict, description="模拟执行后的变量池"
    )


class ActionValidateResponse(SQLModel):
    """验证响应"""

    valid: bool
    action_id: int | str
    action_name: str
    missing_params: list[str]
    invalid_params: list[str]
    errors: list[str]


class ExecuteStepResponse(SQLModel):
    """单步执行响应"""

    step_index: int
    action_id: int | str
    action_name: str
    result: ActionResultResponse


class WorkflowStepExecuteRequest(SQLModel):
    """单步执行工作流请求"""

    browser_id: str = Field(description="浏览器ID")
    steps: list[WorkflowStepRequest] = Field(description="步骤列表")
    step_index: int = Field(description="要执行的步骤索引")
    user_data: dict = Field(default_factory=dict, description="用户数据")
    page_index: int | None = Field(default=None, description="页面索引")


class WorkflowStepExecuteResponse(SQLModel):
    """单步执行工作流响应"""

    success: bool
    step_index: int
    action_id: int | str
    result: Any = None
    error: str | None = None
    duration: float = 0.0
    current_step: int
    total_steps: int

