"""
测试结构化条件模型 —— 覆盖原子条件、复合条件、边界情况
"""
import pytest
from app.models.execution.condition_models import (
    ConditionRule,
    ParamsCondition,
    ConditionValueType,
    LogicOperator,
    evaluate_rule,
    evaluate_condition,
    ConditionEvaluateError,
)


class TestParamsCondition:
    """原子条件测试"""

    def test_boolean_true_match(self):
        c = ParamsCondition(field="flag", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)
        assert evaluate_condition(c, {"flag": True}) is True

    def test_boolean_false_match(self):
        c = ParamsCondition(field="flag", condition_value_type=ConditionValueType.BOOLEAN, condition_value=False)
        assert evaluate_condition(c, {"flag": False}) is True

    def test_boolean_mismatch(self):
        c = ParamsCondition(field="flag", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)
        assert evaluate_condition(c, {"flag": False}) is False

    def test_boolean_wrong_type(self):
        c = ParamsCondition(field="flag", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)
        with pytest.raises(ConditionEvaluateError):
            evaluate_condition(c, {"flag": "not_bool"})

    def test_null_match(self):
        c = ParamsCondition(field="val", condition_value_type=ConditionValueType.NULL, condition_value=None)
        assert evaluate_condition(c, {"val": None}) is True

    def test_null_mismatch(self):
        c = ParamsCondition(field="val", condition_value_type=ConditionValueType.NULL, condition_value=None)
        assert evaluate_condition(c, {"val": "not_none"}) is False

    def test_string_match(self):
        c = ParamsCondition(field="status", condition_value_type=ConditionValueType.STRING, condition_value="ok")
        assert evaluate_condition(c, {"status": "ok"}) is True

    def test_string_mismatch(self):
        c = ParamsCondition(field="status", condition_value_type=ConditionValueType.STRING, condition_value="ok")
        assert evaluate_condition(c, {"status": "fail"}) is False

    def test_missing_field(self):
        c = ParamsCondition(field="missing", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)
        with pytest.raises(ConditionEvaluateError):
            evaluate_condition(c, {"other": True})

    def test_validation_boolean_rejects_string(self):
        with pytest.raises(ValueError):
            ParamsCondition(field="x", condition_value_type=ConditionValueType.BOOLEAN, condition_value="not_bool")

    def test_validation_null_rejects_value(self):
        with pytest.raises(ValueError):
            ParamsCondition(field="x", condition_value_type=ConditionValueType.NULL, condition_value="not_none")

    def test_validation_string_rejects_bool(self):
        with pytest.raises(ValueError):
            ParamsCondition(field="x", condition_value_type=ConditionValueType.STRING, condition_value=True)


class TestConditionRuleSimple:
    """简单条件规则测试（单原子条件）"""

    def test_and_single_true(self):
        rule = ConditionRule(
            logic=LogicOperator.AND,
            condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True),
        )
        assert evaluate_rule(rule, {"a": True}) is True

    def test_and_single_false(self):
        rule = ConditionRule(
            logic=LogicOperator.AND,
            condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True),
        )
        assert evaluate_rule(rule, {"a": False}) is False

    def test_not_single(self):
        rule = ConditionRule(
            logic=LogicOperator.NOT,
            rules=[
                ConditionRule(
                    logic=LogicOperator.AND,
                    condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True),
                ),
            ],
        )
        assert evaluate_rule(rule, {"a": True}) is False
        assert evaluate_rule(rule, {"a": False}) is True

    def test_missing_variable_is_false_by_default(self):
        """变量不存在时，默认视为条件不满足（不抛异常）"""
        rule = ConditionRule(
            logic=LogicOperator.AND,
            condition=ParamsCondition(field="missing", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True),
        )
        assert evaluate_rule(rule, {"other": True}) is False

    def test_missing_variable_strict_mode(self):
        """strict=True 时，变量不存在应该抛异常"""
        rule = ConditionRule(
            logic=LogicOperator.AND,
            condition=ParamsCondition(field="missing", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True),
        )
        with pytest.raises(ConditionEvaluateError):
            evaluate_rule(rule, {"other": True}, strict=True)


class TestConditionRuleCompound:
    """复合条件规则测试"""

    @pytest.fixture
    def vars_ok(self):
        return {"a": True, "b": True, "c": False}

    # ---- AND 组合 ----

    def test_and_all_true(self, vars_ok):
        """A AND B，两者都为 True"""
        rule = ConditionRule(
            logic=LogicOperator.AND,
            rules=[
                ConditionRule(condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ConditionRule(condition=ParamsCondition(field="b", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
            ],
        )
        assert evaluate_rule(rule, vars_ok) is True

    def test_and_one_false(self, vars_ok):
        """A AND C，A=True 但 C=False"""
        rule = ConditionRule(
            logic=LogicOperator.AND,
            rules=[
                ConditionRule(condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ConditionRule(condition=ParamsCondition(field="c", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
            ],
        )
        assert evaluate_rule(rule, vars_ok) is False

    def test_and_with_missing_field(self, vars_ok):
        """A AND missing，missing 不存在 → 视为 False"""
        rule = ConditionRule(
            logic=LogicOperator.AND,
            rules=[
                ConditionRule(condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ConditionRule(condition=ParamsCondition(field="missing", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
            ],
        )
        assert evaluate_rule(rule, vars_ok) is False

    # ---- OR 组合 ----

    def test_or_one_true(self, vars_ok):
        """C OR A，C=False 但 A=True"""
        rule = ConditionRule(
            logic=LogicOperator.OR,
            rules=[
                ConditionRule(condition=ParamsCondition(field="c", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ConditionRule(condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
            ],
        )
        assert evaluate_rule(rule, vars_ok) is True

    def test_or_all_false(self, vars_ok):
        """C OR C，两者都为 False"""
        rule = ConditionRule(
            logic=LogicOperator.OR,
            rules=[
                ConditionRule(condition=ParamsCondition(field="c", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ConditionRule(condition=ParamsCondition(field="c", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
            ],
        )
        assert evaluate_rule(rule, vars_ok) is False

    def test_or_with_missing_field(self, vars_ok):
        """missing OR A，missing 不存在但 A=True → 整体为 True"""
        rule = ConditionRule(
            logic=LogicOperator.OR,
            rules=[
                ConditionRule(condition=ParamsCondition(field="missing", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ConditionRule(condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
            ],
        )
        assert evaluate_rule(rule, vars_ok) is True

    # ---- 嵌套组合 ----

    def test_nested_and_or(self, vars_ok):
        """(C AND B) OR A → (False AND True) OR True = False OR True = True"""
        rule = ConditionRule(
            logic=LogicOperator.OR,
            rules=[
                ConditionRule(
                    logic=LogicOperator.AND,
                    rules=[
                        ConditionRule(condition=ParamsCondition(field="c", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                        ConditionRule(condition=ParamsCondition(field="b", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                    ],
                ),
                ConditionRule(condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
            ],
        )
        assert evaluate_rule(rule, vars_ok) is True

    def test_nested_or_and(self, vars_ok):
        """(A OR C) AND B → (True OR False) AND True = True AND True = True"""
        rule = ConditionRule(
            logic=LogicOperator.AND,
            rules=[
                ConditionRule(
                    logic=LogicOperator.OR,
                    rules=[
                        ConditionRule(condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                        ConditionRule(condition=ParamsCondition(field="c", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                    ],
                ),
                ConditionRule(condition=ParamsCondition(field="b", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
            ],
        )
        assert evaluate_rule(rule, vars_ok) is True

    def test_nested_not_and(self, vars_ok):
        """NOT (C AND B) → NOT (False AND True) = NOT False = True"""
        rule = ConditionRule(
            logic=LogicOperator.NOT,
            rules=[
                ConditionRule(
                    logic=LogicOperator.AND,
                    rules=[
                        ConditionRule(condition=ParamsCondition(field="c", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                        ConditionRule(condition=ParamsCondition(field="b", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                    ],
                ),
            ],
        )
        assert evaluate_rule(rule, vars_ok) is True

    # ---- 混合类型条件 ----

    def test_mixed_types(self):
        """检查 bool + null + string 混合条件"""
        vars_ctx = {"logged_in": True, "error": None, "role": "admin"}
        # (logged_in == True) AND (error IS None) AND (role == "admin")
        rule = ConditionRule(
            logic=LogicOperator.AND,
            rules=[
                ConditionRule(condition=ParamsCondition(field="logged_in", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ConditionRule(condition=ParamsCondition(field="error", condition_value_type=ConditionValueType.NULL, condition_value=None)),
                ConditionRule(condition=ParamsCondition(field="role", condition_value_type=ConditionValueType.STRING, condition_value="admin")),
            ],
        )
        assert evaluate_rule(rule, vars_ctx) is True

    def test_mixed_types_with_wrong_string(self):
        """role != 'admin' 时整体为 False"""
        vars_ctx = {"logged_in": True, "error": None, "role": "user"}
        rule = ConditionRule(
            logic=LogicOperator.AND,
            rules=[
                ConditionRule(condition=ParamsCondition(field="logged_in", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ConditionRule(condition=ParamsCondition(field="error", condition_value_type=ConditionValueType.NULL, condition_value=None)),
                ConditionRule(condition=ParamsCondition(field="role", condition_value_type=ConditionValueType.STRING, condition_value="admin")),
            ],
        )
        assert evaluate_rule(rule, vars_ctx) is False


class TestConditionRuleValidation:
    """条件规则验证测试"""

    def test_condition_and_rules_mutually_exclusive(self):
        with pytest.raises(ValueError):
            ConditionRule(
                logic=LogicOperator.AND,
                condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True),
                rules=[
                    ConditionRule(condition=ParamsCondition(field="b", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ],
            )

    def test_empty_rule(self):
        with pytest.raises(ValueError):
            ConditionRule(logic=LogicOperator.AND)

    def test_not_requires_single_rule(self):
        with pytest.raises(ValueError):
            ConditionRule(
                logic=LogicOperator.NOT,
                rules=[
                    ConditionRule(condition=ParamsCondition(field="a", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                    ConditionRule(condition=ParamsCondition(field="b", condition_value_type=ConditionValueType.BOOLEAN, condition_value=True)),
                ],
            )
