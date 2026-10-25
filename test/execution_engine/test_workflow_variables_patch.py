"""工作流输入变量增量更新（variables_patch）单测

对应 docs/工作流变量增量更新计划书.md：
- 只覆盖 patch 里给出的键；
- 值为 None 表示删除该键；
- 不修改入参（返回新字典）。
"""

from app.services.execution.crud_service.workflow_crud import merge_variables_patch


class TestMergeVariablesPatch:
    def test_only_given_keys_are_overridden(self):
        current = {"llm_model": "openrouter/free", "llm_api_key": "sk-1"}
        merged = merge_variables_patch(current, {"llm_model": "deepseek-chat"})

        assert merged == {"llm_model": "deepseek-chat", "llm_api_key": "sk-1"}

    def test_none_deletes_key(self):
        current = {"a": 1, "b": 2}
        assert merge_variables_patch(current, {"b": None}) == {"a": 1}

    def test_none_on_missing_key_is_noop(self):
        assert merge_variables_patch({"a": 1}, {"zzz": None}) == {"a": 1}

    def test_none_current_treated_as_empty(self):
        assert merge_variables_patch(None, {"a": 1}) == {"a": 1}

    def test_empty_patch_keeps_all(self):
        assert merge_variables_patch({"a": 1}, {}) == {"a": 1}
        assert merge_variables_patch({"a": 1}, None) == {"a": 1}

    def test_patch_does_not_mutate_inputs(self):
        current = {"a": 1}
        patch = {"b": 2, "a": None}
        merge_variables_patch(current, patch)

        assert current == {"a": 1}
        assert patch == {"b": 2, "a": None}

    def test_nested_value_replaced_wholesale(self):
        """数组/对象变量按整体替换（不做深合并，避免半新半旧）"""
        current = {"pool": [{"model": "a"}]}
        merged = merge_variables_patch(current, {"pool": [{"model": "b"}]})

        assert merged == {"pool": [{"model": "b"}]}
