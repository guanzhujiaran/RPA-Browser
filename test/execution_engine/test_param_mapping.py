"""循环 param_mapping 注入单测（点路径目标键）

背景：定位器取代裸 ``selector`` 后，循环项要注入的是 ``locator.value`` 这样的嵌套键，
因此映射目标键必须支持点路径；同时保持既有约束（只注入步骤已声明的参数、
不改原对象）。
"""

from __future__ import annotations

import pytest

from app.models.execution.action_params import ClickParams
from app.models.execution.variable_specs import _scan_schema_template_variables
from app.services.execution.param_mapping import apply_param_mapping


def test_flat_key_injected_when_declared():
    merged, applied = apply_param_mapping({"timeout": 1000}, {"timeout": 5000})

    assert applied == ["timeout"]
    assert merged["timeout"] == 5000


def test_undeclared_key_is_not_added():
    merged, applied = apply_param_mapping({"timeout": 1000}, {"value": "x"})

    assert applied == []
    assert "value" not in merged


def test_dotted_key_replaces_nested_value():
    raw = {"locator": {"value": "#static", "pick": "first"}}
    merged, applied = apply_param_mapping(raw, {"locator.value": "https://x/1"})

    assert applied == ["locator.value"]
    assert merged["locator"]["value"] == "https://x/1"
    assert merged["locator"]["pick"] == "first", "同层其它字段不受影响"
    assert raw["locator"]["value"] == "#static", "不得改动原对象"


def test_dotted_key_skipped_when_parent_missing():
    merged, applied = apply_param_mapping({"timeout": 1}, {"locator.value": "#x"})

    assert applied == []
    assert "locator" not in merged


def test_dotted_key_skipped_when_parent_not_object():
    merged, applied = apply_param_mapping({"locator": "#flat"}, {"locator.value": "#x"})

    assert applied == []
    assert merged["locator"] == "#flat"


def test_locator_template_value_becomes_input_variable():
    """嵌套定位器里的 {{变量}} 也要被发现（契约提示不因结构变深而丢失）"""
    nested_schema = {
        "properties": {
            "locator": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "default": "{{item.link}}"},
                    "within": {"type": "string", "default": ".box"},
                },
            },
            "fallbacks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "default": "{{fallback_selector}}"}
                    },
                },
            },
        }
    }

    names = {s.name for s in _scan_schema_template_variables(nested_schema)}

    assert names == {"item.link", "fallback_selector"}


def test_params_schema_exposes_locator_not_selector():
    """参数 schema 只暴露 locator（旧 selector 仅作为入参兼容，不出现在表单里）"""
    schema = ClickParams.model_json_schema()

    assert "locator" in schema["properties"]
    assert "selector" not in schema["properties"]
    assert "fallbacks" in schema["properties"]
