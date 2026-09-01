"""条件模块类型定义

提供精细化的布尔值、空值、字符串条件匹配能力（完全不使用 eval）。
用户只能传入参数名称 + 判断类型 + 期望值，系统通过纯 Python 逻辑进行安全评估。
"""

from __future__ import annotations

from bili_common.models import StrEnumAutoDoc
from typing import  List, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator


# ---- 枚举 ----

class ConditionValueType(StrEnumAutoDoc):
    """条件值类型 —— 限制用户只能使用这三种类型做判断"""
    BOOLEAN = "BOOLEAN"  # True / False
    NULL = "NULL"        # None
    STRING = "STRING"    # 字符串精确匹配


class LogicOperator(StrEnumAutoDoc):
    """逻辑运算符 —— 支持 AND / OR / NOT 组合多个原子条件"""
    AND = "AND"
    OR = "OR"
    NOT = "NOT"


# ---- 条件值类型别名 ----
ConditionValue = bool| None| str


# ---- 原子条件 ----

class ParamsCondition(BaseModel):
    """单个原子条件：检查 variables 上下文中某字段值是否匹配期望值

    使用示例:
        # 检查 is_logged_in 是否为 True
        ParamsCondition(
            field="is_logged_in",
            condition_value_type=ConditionValueType.BOOLEAN,
            condition_value=True,
        )

        # 检查 error_msg 是否为空
        ParamsCondition(
            field="error_msg",
            condition_value_type=ConditionValueType.NULL,
            condition_value=None,
        )

        # 检查 status 是否等于 "success"
        ParamsCondition(
            field="status",
            condition_value_type=ConditionValueType.STRING,
            condition_value="success",
        )
    """

    model_config = ConfigDict(frozen=True)

    field: str = Field(..., description="要检查的变量名（对应 variables 中的 key）")
    condition_value_type: ConditionValueType = Field(
        ..., description="条件值类型: BOOLEAN / NULL / STRING"
    )
    condition_value: ConditionValue = Field(..., description="期望匹配的值")
    description: Optional[str] = Field(default=None, description="可选描述（便于调试）")

    @model_validator(mode="after")
    def _validate_type_consistency(self) -> "ParamsCondition":
        """确保 condition_value 的实际类型与 condition_value_type 声明一致"""
        cvt = self.condition_value_type
        cv = self.condition_value
        if cvt == ConditionValueType.BOOLEAN:
            if not isinstance(cv, bool):
                raise ValueError(
                    f"BOOLEAN 类型的 condition_value 必须是 bool，实际为: {type(cv).__name__}"
                )
        elif cvt == ConditionValueType.NULL:
            if cv is not None:
                raise ValueError(
                    f"NULL 类型的 condition_value 必须是 None，实际为: {cv!r}"
                )
        elif cvt == ConditionValueType.STRING:
            if not isinstance(cv, str):
                raise ValueError(
                    f"STRING 类型的 condition_value 必须是 str，实际为: {type(cv).__name__}"
                )
        return self


# ---- 复合条件规则 ----

class ConditionRule(BaseModel):
    """可递归组合的条件规则 —— 支持 AND / OR / NOT 组合多个原子条件

    使用示例:
        # 简单条件
        ConditionRule(
            logic=LogicOperator.AND,
            condition=ParamsCondition(
                field="logged_in",
                condition_value_type=ConditionValueType.BOOLEAN,
                condition_value=True,
            ),
        )

        # 组合条件: (A AND B) OR C
        ConditionRule(
            logic=LogicOperator.OR,
            rules=[
                ConditionRule(
                    logic=LogicOperator.AND,
                    rules=[
                        ConditionRule(condition=ParamsCondition(...)),  # A
                        ConditionRule(condition=ParamsCondition(...)),  # B
                    ],
                ),
                ConditionRule(condition=ParamsCondition(...)),  # C
            ],
        )
    """

    model_config = ConfigDict(frozen=True)

    logic: LogicOperator = Field(default=LogicOperator.AND, description="组合逻辑: AND / OR / NOT")
    condition: Optional[ParamsCondition] = Field(
        default=None, description="原子条件（叶子节点，与 rules 互斥）"
    )
    rules: Optional[List["ConditionRule"]] = Field(
        default=None, description="子规则列表（分支节点，与 condition 互斥）"
    )
    description: Optional[str] = Field(default=None, description="可选描述（便于调试）")

    @model_validator(mode="after")
    def _validate_structure(self) -> "ConditionRule":
        has_c = self.condition is not None
        has_r = self.rules is not None and len(self.rules) > 0
        if has_c and has_r:
            raise ValueError("condition（原子条件）与 rules（子规则）互斥，只能提供其中一个")
        if not has_c and not has_r:
            raise ValueError("必须提供 condition（原子条件）或 rules（子规则列表）")
        if self.logic == LogicOperator.NOT:
            if not has_r or len(self.rules) != 1:
                raise ValueError("NOT 运算符要求 rules 恰好包含 1 个子规则")
        return self


# ---- 轻量评估函数（纯 Python 逻辑，零 eval） ----

class ConditionEvaluateError(Exception):
    """条件评估异常"""


def evaluate_condition(condition: ParamsCondition, variables: dict) -> bool:
    """对 variables 上下文评估单个原子条件（不使用 eval）

    Args:
        condition: 原子条件
        variables: 变量字典（键值对上下文）

    Returns:
        条件是否满足

    Raises:
        ConditionEvaluateError: 变量不存在或类型不匹配时
    """
    field = condition.field
    cvt = condition.condition_value_type
    expected = condition.condition_value

    if field not in variables:
        raise ConditionEvaluateError(
            f"变量 '{field}' 不在当前上下文中（可用变量: {list(variables.keys())}"
        )

    actual = variables[field]

    if cvt == ConditionValueType.BOOLEAN:
        if not isinstance(actual, bool):
            raise ConditionEvaluateError(
                f"BOOLEAN 条件要求变量 '{field}' 类型为 bool，实际: {type(actual).__name__}"
            )
        return actual is expected

    elif cvt == ConditionValueType.NULL:
        return actual is None

    elif cvt == ConditionValueType.STRING:
        if not isinstance(actual, str):
            raise ConditionEvaluateError(
                f"STRING 条件要求变量 '{field}' 类型为 str，实际: {type(actual).__name__}"
            )
        return actual == expected

    raise ConditionEvaluateError(f"不支持的条件值类型: {cvt}")


def evaluate_rule(rule: ConditionRule, variables: dict, *, strict: bool = False) -> bool:
    """递归评估 ConditionRule（完全不使用 eval，纯 Python 逻辑）

    Args:
        rule: 条件规则（可能是原子条件或组合规则）
        variables: 变量字典
        strict: 是否严格模式。True 时变量不存在或类型不匹配会抛出异常；
                False 时（默认）视为条件不满足，返回 False。

    Returns:
        规则评估结果（True/False）

    Raises:
        ConditionEvaluateError: 仅在 strict=True 时抛出
    """
    if rule.condition is not None:
        try:
            return evaluate_condition(rule.condition, variables)
        except ConditionEvaluateError:
            if strict:
                raise
            return False

    if rule.rules is not None:
        results = [evaluate_rule(r, variables, strict=strict) for r in rule.rules]
        if rule.logic == LogicOperator.AND:
            return all(results)
        elif rule.logic == LogicOperator.OR:
            return any(results)
        elif rule.logic == LogicOperator.NOT:
            return not results[0]

    raise ConditionEvaluateError("条件规则为空，无法评估")
