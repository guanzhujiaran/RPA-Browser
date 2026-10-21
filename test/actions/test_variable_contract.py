"""动作变量契约（VariableSpec / compute_action_variable_contract）单测

对应计划书 docs/动作变量契约反射计划书.md：
- 显式 ClassVar 声明优先，{{...}} 模板扫描兜底，同名去重声明优先；
- 扫描只看 const/default，不扫 description（描述里的 {{变量}} 是文档示例）；
- ClassVar 契约不进 model_json_schema / 不参与实例序列化。
"""

from typing import ClassVar

from app.models.execution.action_params import (
    BaseActionParams,
    BuiltinActionType,
    VariableSpec,
    compute_action_variable_contract,
)


class _DummyParams(BaseActionParams):
    """测试用参数模型：显式声明 + 模板默认值 + 描述示例三种情况"""

    url: str = ""  # noqa: 不参与契约
    target: str = ""
    note: str = ""

    input_variables: ClassVar[list[VariableSpec]] = [
        VariableSpec(name="target", required=True, description="显式声明的目标"),
    ]


def test_explicit_declaration_wins_and_classvar_not_in_schema():
    schema = _DummyParams.model_json_schema()
    # ClassVar 契约不进 schema
    assert "input_variables" not in schema.get("properties", {})
    assert "output_variables" not in schema.get("properties", {})
    # 实例序列化也不携带
    assert "input_variables" not in _DummyParams().model_dump()

    inputs, outputs = compute_action_variable_contract(_DummyParams)
    assert [v.name for v in inputs] == ["target"]
    assert inputs[0].required is True
    assert outputs == []


def test_scan_default_template_variables():
    class ScanParams(BaseActionParams):
        target: str = ""
        note: str = ""

    inputs, outputs = compute_action_variable_contract(
        ScanParams,
        json_schema={
            "properties": {
                "target": {
                    "type": "string",
                    "default": "{{dynamic_id}}",
                    "description": "支持 {{某示例}} 模板的说明文案",
                },
                "note": {"type": "string", "description": "{{描述示例}} 不应被扫描"},
            }
        },
    )
    # 只扫 default/const，description 里的示例不算
    assert [v.name for v in inputs] == ["dynamic_id"]
    assert inputs[0].required is False
    assert outputs == []


def test_builtin_metadata_carries_contract():
    meta = BuiltinActionType.GET_TEXT.metadata
    assert [v.name for v in meta.output_variables] == ["text", "items"]
    assert BuiltinActionType.CLICK.metadata.output_variables == []


def test_compute_without_model_returns_empty():
    inputs, outputs = compute_action_variable_contract(None)
    assert inputs == []
    assert outputs == []
