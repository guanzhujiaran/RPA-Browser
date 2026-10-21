"""变量契约：动作自描述需要/产出哪些变量

从 ``action_params`` 拆出。包含：

- ``VariableSpec``：单个变量槽位的声明（名称/类型/必填/说明/默认值）；
- ``extract_template_variables``：从文本中提取 ``{{var}}`` 模板变量名；
- ``compute_action_variable_contract``：由参数模型（类级 ClassVar 声明 +
  JSON Schema ``{{...}}`` 模板扫描兜底）推导 (input_variables, output_variables)。

复合动作（自定义动作）的递归聚合见
``app.services.execution.variable_contract``。
"""

from __future__ import annotations

import contextlib
import re
from typing import Any, Literal

from pydantic import Field
from sqlmodel import SQLModel

# ============ 变量契约（动作自描述需要/产出哪些变量） ============

# 与 Scope._TEMPLATE_RE 同语义：{{var}} / {{a.b}} 点路径模板
_VARIABLE_RE = re.compile(r"\{\{([\w.]+)\}\}")

VariableType = Literal["string", "number", "boolean", "array", "object"]


class VariableSpec(SQLModel):
    """变量契约定义：声明动作消费/产出的变量槽位（动作元数据的一部分）"""

    name: str = Field(description="变量名（支持点路径，如 item.dynId）")
    type: VariableType = Field(default="string", description="变量类型")
    required: bool = Field(default=False, description="是否必填（缺失时执行报错）")
    description: str = Field(default="", description="变量说明")
    default: Any | None = Field(default=None, description="缺省值")


def extract_template_variables(text: str | None) -> list[str]:
    """提取文本中的 {{var}} 模板变量名（保持出现顺序去重）"""
    if not text:
        return []
    seen: list[str] = []
    for m in _VARIABLE_RE.finditer(text):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def _declared_variables(params_model: type[SQLModel] | None, attr: str) -> list[VariableSpec]:
    """读取参数模型上的类级变量契约声明（BaseActionParams.input_variables / output_variables）"""
    if params_model is None:
        return []
    declared = getattr(params_model, attr, None) or []
    return [
        v if isinstance(v, VariableSpec) else VariableSpec.model_validate(v)
        for v in declared
    ]


def _scan_schema_template_variables(json_schema: dict | None) -> list[VariableSpec]:
    """扫描参数 JSON Schema 的 string 字段取值（const/default），提取 {{var}} 模板变量。

    兜底机制：未显式声明 input_variables 的动作也能从既有 {{...}} 模板默认值反射出变量提示。
    刻意**不扫 description**——描述文案里的 {{变量}} 多为文档示例而非真实引用（会产出假变量）。
    扫描发现的变量 required=False（无法静态判断是否必填）。
    """
    if not json_schema:
        return []
    specs: list[VariableSpec] = []
    seen: set[str] = set()
    for prop in (json_schema.get("properties") or {}).values():
        if not isinstance(prop, dict):
            continue
        # 仅扫描文本类字段的取值（无 type 或 string；anyOf 等复合结构不扫）
        if prop.get("type") not in (None, "string"):
            continue
        for text in (prop.get("const"), prop.get("default")):
            for name in extract_template_variables(text if isinstance(text, str) else None):
                if name in seen:
                    continue
                seen.add(name)
                specs.append(
                    VariableSpec(
                        name=name,
                        type="string",
                        required=False,
                        description="由参数 {{...}} 模板自动扫描发现",
                    )
                )
    return specs


def compute_action_variable_contract(
    params_model: type[SQLModel] | None,
    json_schema: dict | None = None,
) -> tuple[list[VariableSpec], list[VariableSpec]]:
    """计算动作变量契约：(input_variables, output_variables)。

    合并规则：显式类级声明（BaseActionParams 子类 ClassVar）优先，
    `{{...}}` 模板扫描结果补充（同名去重，声明优先），产出变量只认显式声明
    （执行结果由运行时决定，无法静态扫描推断）。
    """
    if json_schema is None and params_model is not None and hasattr(params_model, "model_json_schema"):
        with contextlib.suppress(Exception):
            json_schema = params_model.model_json_schema()
    inputs: dict[str, VariableSpec] = {
        v.name: v for v in _declared_variables(params_model, "input_variables")
    }
    for spec in _scan_schema_template_variables(json_schema):
        inputs.setdefault(spec.name, spec)
    outputs = _declared_variables(params_model, "output_variables")
    return list(inputs.values()), outputs
