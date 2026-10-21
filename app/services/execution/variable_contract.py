"""自定义动作（CompositeAction）变量契约聚合

从步骤树静态推导复合动作对外的输入/输出变量契约：

- 输入契约 = 各步骤动作（内置契约 / 递归引用的自定义契约）的 ``input_variables`` 并集
  减去内部自产变量（各步骤 ``output_vars`` 动态赋值名 ∪ 各动作声明的
  ``output_variables`` 槽位名）——内部自产的变量不应出现在对外契约里；
- 输出契约 = 各动作声明 ``output_variables`` 的并集（槽位语义，
  实际写入的变量名由引用方 step 的 ``output_vars`` 决定）。

纯静态推导，不执行任何动作；DB 读取仅用于展开嵌套的自定义动作引用。
"""

from typing import Any

from app.models.execution.action_params import (
    BuiltinActionType,
    VariableSpec,
    compute_action_variable_contract,
)
from app.models.execution.action_params import normalize_composite_action_id

# 分支步骤所在的 params 键（与 LoopParams / IfElseParams 对齐）
_BRANCH_PARAM_KEYS = ("TrueBranch", "FalseBranch", "loopBranch")
# 顶层嵌套步骤列表键
_TOP_LEVEL_STEP_KEYS = ("children", "on_error_branch")


def _merge_specs(target: dict[str, VariableSpec], specs: list[VariableSpec]) -> None:
    """按变量名合并契约（先到者优先，保留首次出现的类型/必填信息）"""
    for spec in specs:
        target.setdefault(spec.name, spec)


def _iter_nested_steps(step: dict) -> list[Any]:
    """取一个步骤下嵌套的子步骤列表（分支 / 子步骤 / 错误分支）"""
    nested: list[Any] = []
    params = step.get("params") or {}
    params_dict = params if isinstance(params, dict) else None
    for key in _BRANCH_PARAM_KEYS:
        branch = (params_dict or {}).get(key) if params_dict else None
        if isinstance(branch, list):
            nested.extend(branch)
    for key in _TOP_LEVEL_STEP_KEYS:
        branch = step.get(key)
        if isinstance(branch, list):
            nested.extend(branch)
    return nested


def _merge_contract(
    inputs: dict[str, VariableSpec],
    outputs: dict[str, VariableSpec],
    contract: tuple[list[VariableSpec], list[VariableSpec]],
) -> None:
    _merge_specs(inputs, contract[0])
    _merge_specs(outputs, contract[1])


async def compute_custom_action_variable_contract(
    steps: list[Any],
    _visited: set[int] | None = None,
) -> tuple[list[VariableSpec], list[VariableSpec]]:
    """递归聚合步骤树，得到复合动作对外变量契约 (input_variables, output_variables)

    Args:
        steps: 步骤列表（WorkflowStep 模型或 dict，含嵌套分支）
        _visited: 已展开的自定义动作 ID（防循环引用）

    Returns:
        (input_variables, output_variables)
    """
    visited = _visited if _visited is not None else set()
    inputs: dict[str, VariableSpec] = {}
    outputs: dict[str, VariableSpec] = {}
    # 内部自产变量：动态赋值名（step.output_vars）+ 动作声明产出槽位名
    produced: set[str] = set()

    for step in steps or []:
        step_dict = step if isinstance(step, dict) else (
            step.model_dump() if hasattr(step, "model_dump") else dict(step)
        )
        if not isinstance(step_dict, dict):
            continue

        # 1. 动态赋值的输出名：调用方用 output_vars 决定结果写入哪些变量
        for name in step_dict.get("output_vars") or []:
            if isinstance(name, str) and name:
                produced.add(name)

        # 2. 本步骤动作的契约：内置直接查，自定义递归展开
        action_type = step_dict.get("action_type") or step_dict.get("action_id")
        contract: tuple[list[VariableSpec], list[VariableSpec]] | None = None
        try:
            builtin = BuiltinActionType(action_type)
        except ValueError:
            builtin = None
        if builtin is not None:
            contract = compute_action_variable_contract(builtin.params_model)
        else:
            custom_id = normalize_composite_action_id(step_dict.get("action_id"))
            if custom_id and custom_id not in visited:
                visited.add(custom_id)
                from app.services.execution.action_registry import action_registry

                nested_steps = await action_registry.get_custom_action_steps(custom_id)
                if nested_steps:
                    _merge_contract(
                        inputs, outputs,
                        await compute_custom_action_variable_contract(
                            nested_steps, visited
                        ),
                    )

        if contract is not None:
            _merge_contract(inputs, outputs, contract)

        # 3. 嵌套子步骤（分支 / 错误分支 / children）同样参与聚合
        nested_contract = await compute_custom_action_variable_contract(
            _iter_nested_steps(step_dict), visited
        )
        _merge_contract(inputs, outputs, nested_contract)

    # 对外输入契约 = 消费变量 − 内部自产变量
    external_inputs = [spec for name, spec in inputs.items() if name not in produced]
    return external_inputs, list(outputs.values())
