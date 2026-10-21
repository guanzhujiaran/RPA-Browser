"""工作流运行前置校验

从 ``workflow_runner`` 拆出：执行目标解析、被引用动作的步骤加载与归一化、
配置类失败（``WorkflowRunnerError``）的定义。

只做「开跑前能静态判定的问题」；执行期异常由 ``run_workflow`` 收敛。
"""

from __future__ import annotations

from typing import Any

from app.models.database.workflow.models import UserWorkflow
from app.services.execution.crud_service import action_crud_svr
from app.models.execution.action_params import (
    _ensure_action_type,
    workflow_step_adapter,
)


class WorkflowRunnerError(Exception):
    """工作流运行前置校验失败（配置类问题，非执行异常）"""


async def _resolve_target_browser_id(
    workflow: UserWorkflow, browser_id: int | None
) -> int:
    """解析执行目标浏览器：手动运行优先用入参，否则用工作流上配置的"""
    target = browser_id if browser_id is not None else workflow.browser_id
    if target is None:
        raise WorkflowRunnerError("工作流未配置执行目标浏览器")
    return int(target)


async def _load_normalized_steps(workflow: UserWorkflow) -> tuple[list[Any], Any]:
    """加载并归一化被引用 action 的步骤

    Returns:
        (归一化步骤列表, 被引用的动作模型)；动作模型用于组装运行时变量池
        （`input_vars` 声明），避免这里为了拿变量再查一次库。
    """
    if not workflow.custom_action_id:
        raise WorkflowRunnerError("工作流未关联任何动作")

    action_model = await action_crud_svr.get_by_action_id(workflow.custom_action_id)
    if not action_model:
        raise WorkflowRunnerError(f"关联的动作不存在: {workflow.custom_action_id}")
    if not getattr(action_model, "is_enabled", True):
        raise WorkflowRunnerError(f"关联的动作已被禁用: {workflow.custom_action_id}")

    normalized: list[Any] = []
    for step in action_model.steps or []:
        if isinstance(step, dict):
            step = workflow_step_adapter.validate_python(_ensure_action_type(step))
        normalized.append(step)
    if not normalized:
        raise WorkflowRunnerError(
            f"关联的动作没有任何步骤: {workflow.custom_action_id}"
        )
    return normalized, action_model


def _build_run_variables(workflow: UserWorkflow, action_model: Any) -> dict[str, Any]:
    """组装运行时变量池：动作声明默认值 ← 工作流配置值，并校验必填项

    - 变量**定义**以动作为准（`input_vars` 声明，含 `default` / `required`），
      工作流只存**值**（`UserWorkflow.variables`），因此动作契约变更后无需迁移数据；
    - 同名变量以工作流配置为准（允许覆盖默认值）；
    - 必填变量缺失（None / 空串）直接前置校验失败 → 运行记录 failed + 结束通知，
      避免带着未解析的 `{{变量}}` 跑到动作里才炸。

    Returns:
        传给 `WorkflowExecutionRequest.variables` 的变量池。
    """
    variables: dict[str, Any] = {}
    declarations = list(getattr(action_model, "input_vars", None) or [])
    for spec in declarations:
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        if name:
            variables[str(name)] = spec.get("default")

    overrides = getattr(workflow, "variables", None) or {}
    if isinstance(overrides, dict):
        for name, value in overrides.items():
            if name:
                variables[str(name)] = value

    missing = [
        str(spec["name"])
        for spec in declarations
        if isinstance(spec, dict)
        and spec.get("required")
        and spec.get("name")
        and variables.get(str(spec["name"])) in (None, "")
    ]
    if missing:
        raise WorkflowRunnerError(f"缺少必填输入变量: {', '.join(missing)}")
    return variables
