"""安全表达式求值（safe_eval）字面量兼容测试

背景：工作流 JSON 由前端编辑器手写，条件表达式里常写成 JS 风格的
``share_closed == true`` / ``captcha_shown == false``。未做字面量别名时，
``true`` 会被 AST 当成变量名 → ``NameError`` → 表达式恒 False → if_else
静默走错分支（线上表现为「参与成功却记 failed，去重永远不生效」）。

覆盖：JSON/JS 字面量、Python 字面量、未定义变量兜底、白名单拒绝项。
"""

import pytest

from app.services.execution.actions.safe_eval import safe_evaluate_condition


class TestNameLiterals:
    """true / false / null 与 True / False / None 等价"""

    @pytest.mark.parametrize("literal", ["true", "True"])
    def test_true_literal(self, literal: str):
        assert safe_evaluate_condition(f"flag == {literal}", {"flag": True}) is True
        assert safe_evaluate_condition(f"flag == {literal}", {"flag": False}) is False

    @pytest.mark.parametrize("literal", ["false", "False"])
    def test_false_literal(self, literal: str):
        assert (
            safe_evaluate_condition(f"captcha_shown == {literal}", {"captcha_shown": False})
            is True
        )
        assert (
            safe_evaluate_condition(f"captcha_shown == {literal}", {"captcha_shown": True})
            is False
        )

    @pytest.mark.parametrize("literal", ["null", "None"])
    def test_null_literal(self, literal: str):
        assert safe_evaluate_condition(f"v == {literal}", {"v": None}) is True
        assert safe_evaluate_condition(f"v == {literal}", {"v": 0}) is False

    def test_literal_mixed_with_and(self):
        """线上真实写法：captcha_shown == false and comment_text != ''"""
        expression = "captcha_shown == false and comment_text != ''"
        assert (
            safe_evaluate_condition(
                expression, {"captcha_shown": False, "comment_text": "抽奖"}
            )
            is True
        )
        assert (
            safe_evaluate_condition(
                expression, {"captcha_shown": True, "comment_text": "抽奖"}
            )
            is False
        )
        assert (
            safe_evaluate_condition(
                expression, {"captcha_shown": False, "comment_text": ""}
            )
            is False
        )

    def test_literal_overrides_same_name_variable(self):
        """变量池里若有同名脏值，也不能把布尔字面量覆盖掉"""
        assert safe_evaluate_condition("flag == true", {"flag": True, "true": False}) is True


class TestExistingBehaviour:
    """既有语义回归：不能被字面量别名改坏"""

    def test_undefined_variable_falls_back_to_false(self):
        assert safe_evaluate_condition("nope == 1", {}) is False

    def test_membership_and_arithmetic(self):
        assert safe_evaluate_condition("me in names", {"me": "a", "names": ["a"]}) is True
        assert safe_evaluate_condition("a + 1 == 3", {"a": 2}) is True

    def test_disallowed_syntax_rejected(self):
        """白名单外的语法（函数调用 / 属性访问）直接判 False，不执行"""
        assert safe_evaluate_condition("__import__('os').getcwd() != ''", {}) is False
