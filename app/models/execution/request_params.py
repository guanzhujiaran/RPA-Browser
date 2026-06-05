"""
执行模块参数验证模型

所有操作参数模型继承自 BaseActionParams，实现类型复用和参数验证统一。
"""
from typing import Any
from sqlmodel import SQLModel, Field

from app.models.execution.action_params import AllActionParams

# ============ 执行请求参数模型 ============

class ExecutionRequest(SQLModel):
    """执行请求基础参数"""
    mid: int = Field(description="用户ID")
    browser_id: int = Field(description="浏览器ID")
    variables: dict[str, Any] = Field(default_factory=dict, description="变量池")
    page_index: int | None = Field(default=None, description="页面索引")


class ActionExecutionRequest(ExecutionRequest):
    """操作执行请求参数"""
    action_id: str = Field(description="操作ID")
    params: dict[str, Any] | AllActionParams | None = Field(default=None, description="操作参数")
    input_data: dict[str, Any] = Field(
        default_factory=dict, description="输入数据")
    output: list[str] = Field(default_factory=list, description="输出字段列表")


class StepExecutionRequest(ExecutionRequest):
    """步骤执行请求参数"""
    action_id: str = Field(description="操作ID")
    params: dict[str, Any] | AllActionParams | None = Field(default=None, description="操作参数")
    step_index: int = Field(default=0, description="步骤索引")


class WorkflowExecutionRequest(ExecutionRequest):
    """工作流执行请求参数"""
    action_id: str = Field(description="操作ID")
    workflow_id: str | None = Field(default=None, description="工作流ID（用于关联插件）")
    input_data: dict[str, Any] = Field(
        default_factory=dict, description="输入数据")
    output: list[str] = Field(default_factory=list, description="输出字段列表")


def params_to_dict(params: AllActionParams | dict[str, Any] | None) -> dict[str, Any]:
    """将 params 统一转为 dict，兼容 Pydantic 模型和原生 dict"""
    if params is None:
        return {}
    if isinstance(params, dict):
        return params
    if hasattr(params, 'model_dump'):
        return params.model_dump()
    return dict(params)

