"""操作元数据模型（内部使用 / API 返回）

从 ``action_params`` 拆出：

- ``ActionParameter``：单个参数的 JSON Schema 包装；
- ``ActionMetadata``：内置操作的完整元数据；
- ``ActionMetadataResponse``：动作元数据查询接口的响应模型；
- ``build_action_metadata``：由 ``BuiltinActionType`` 构建 ``ActionMetadata``
  （原为枚举上的 ``metadata`` 属性，因需要引用参数模型与变量契约计算、
  会和枚举形成循环依赖而移入本模块）。
"""

from __future__ import annotations

import contextlib

from pydantic import Field
from sqlmodel import SQLModel

from app.models.execution.action_param_models import BUILTIN_ACTION_PARAMS_MAP
from app.models.execution.builtin_action_types import (
    BuiltinActionType,
    register_metadata_builder,
)
from app.models.execution.variable_specs import (
    VariableSpec,
    compute_action_variable_contract,
)


class ActionParameter(SQLModel):
    """操作参数定义（内部使用）"""

    name: str = Field(description="参数名称")
    json_schema: dict = Field(description="完整的 JSON Schema")


class ActionMetadata(SQLModel):
    """操作元数据（内部使用）"""

    id: BuiltinActionType = Field(description="操作ID")
    name: str = Field(description="操作名称")
    type: BuiltinActionType = Field(description="操作类型")
    description: str = Field(default="", description="操作描述")
    icon_series: int = Field(
        default=0, description="默认展示图标系列编号（0 表示由前端内置图标兜底）"
    )
    icon_id: int = Field(
        default=0, description="默认展示图标在系列内的编号（0 表示由前端内置图标兜底）"
    )
    parameters: list[ActionParameter] = Field(
        default_factory=list, description="参数列表"
    )
    input_variables: list[VariableSpec] = Field(
        default_factory=list,
        description="输入变量契约：动作运行时消费的 {{变量}} 清单",
    )
    output_variables: list[VariableSpec] = Field(
        default_factory=list,
        description="输出变量契约：动作执行产出的变量槽位清单",
    )
    json_schema: dict | None = Field(default=None, description="完整的 JSON Schema")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False, description="错误时重试")
    retry_times: int = Field(default=0, description="重试次数")
    retry_delay: float = Field(default=1.0, description="重试延迟(秒)")
    requires_browser: bool = Field(default=True, description="是否需要浏览器上下文")


class ActionMetadataResponse(SQLModel):
    """操作元数据响应（API 返回）"""

    action_id: str = Field(description="预设操作ID")
    action_type: BuiltinActionType = Field(description="操作类型")
    name: str = Field(default="", description="操作中文名")
    icon_series: int = Field(
        default=0, description="默认展示图标系列编号（0 表示前端内置图标兜底）"
    )
    icon_id: int = Field(
        default=0, description="默认展示图标在系列内的编号（0 表示前端内置图标兜底）"
    )
    json_schema: dict = Field(description="完整的 JSON Schema")
    input_variables: list[VariableSpec] = Field(
        default_factory=list,
        description="输入变量契约：动作运行时消费的 {{变量}} 清单",
    )
    output_variables: list[VariableSpec] = Field(
        default_factory=list,
        description="输出变量契约：动作执行产出的变量槽位清单",
    )


def build_action_metadata(action_type: BuiltinActionType) -> ActionMetadata:
    """构建内置操作的 ActionMetadata（参数 Schema + 图标 + 变量契约）

    原为 ``BuiltinActionType.metadata`` 属性；因需引用参数模型注册表与变量
    契约计算、和枚举构成循环依赖，移入本模块以模块级 import 汇聚。
    """
    params_model = action_type.params_model
    json_schema: dict = {}
    parameters: list[ActionParameter] = []
    if hasattr(params_model, "model_json_schema"):
        with contextlib.suppress(Exception):
            json_schema = params_model.model_json_schema()
            properties = json_schema.get("properties", {})
            for prop_name, prop_schema in properties.items():
                parameters.append(
                    ActionParameter(
                        name=prop_name,
                        json_schema=prop_schema,
                    )
                )
    icon_series, icon_id = action_type.icon
    input_variables, output_variables = compute_action_variable_contract(
        params_model, json_schema
    )
    return ActionMetadata(
        id=action_type,
        name=action_type.nameDisplay,
        type=action_type,
        description=action_type.descDisplay,
        parameters=parameters,
        json_schema=json_schema,
        icon_series=icon_series,
        icon_id=icon_id,
        input_variables=input_variables,
        output_variables=output_variables,
    )


# 晚期绑定：把元数据构建器注册进 BuiltinActionType.metadata 属性。
# 枚举模块无法在文件头 import 本模块（ActionMetadata 的字段注解反向依赖
# 枚举，直接 import 会成环），故由本模块在加载末尾完成注入；任何经
# ``action_params`` 门面或直接 import 本模块的调用方均可使用该属性。
register_metadata_builder(build_action_metadata)
