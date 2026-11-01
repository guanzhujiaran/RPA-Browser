"""安全条件表达式求值（AST 白名单）

从 ``control_flow`` 拆出的叶子模块：不依赖任何 app 模块，仅用标准库
``ast`` / ``operator`` 对受限表达式求值，替代 ``eval``。

白名单：
- 字面量 / 变量名（须在 variables 中）/ dict 下标
- 比较（== != < <= > >= in not in is is not）、布尔（and or）、
  一元（not - +）、二元（+ - * / % **）
- list / tuple 字面量

JSON / JS 风格字面量：``true`` / ``false`` / ``null`` 与 Python 的
``True`` / ``False`` / ``None`` 等价（工作流 JSON 由前端编辑器手写，
写成小写是常态）。这是表达式语言本身的兼容能力，与具体站点/业务无关。
小写字面量优先于同名变量，避免变量池里的脏值把布尔字面量覆盖成真值。

使用方：``control_flow``（策略层条件评估）、``pipeline``（IfElseStep 条件评估）。
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from loguru import logger


#: JSON / JS 风格布尔与空值字面量（大小写不敏感地写成小写时按字面量解析）
_NAME_LITERALS: dict[str, Any] = {
    "true": True,
    "false": False,
    "null": None,
}


def safe_evaluate_condition(condition: str | None, variables: dict) -> bool:
    """安全评估条件表达式（AST 白名单方式，不使用 eval）

    variables 中的键可直接作为变量名引用，例如：
      element_found == True
      loop_index >= 5
    """
    if not condition:
        return False
    try:
        tree = ast.parse(condition.strip(), mode="eval")
        return _eval_ast_node(tree.body, variables)
    except Exception as e:
        logger.warning(f"条件评估失败: {e}")
        return False


def _eval_ast_node(node: ast.AST, variables: dict) -> Any:
    """递归安全求值 AST 节点，仅允许白名单中的节点类型"""
    match node:
        case ast.Constant(value):
            return value
        case ast.Name(id):
            # JSON / JS 风格字面量优先：`share_closed == true` 这类写法在未做别名时
            # 会被当成未定义变量 → 表达式恒 False，静默走错分支（曾导致抽奖动作
            # 永远记 failed、去重永远不生效）。
            if id in _NAME_LITERALS:
                return _NAME_LITERALS[id]
            if id in variables:
                return variables[id]
            raise NameError(f"变量 '{id}' 未定义")
        case ast.Subscript():
            obj = _eval_ast_node(node.value, variables)
            key = _eval_ast_node(node.slice, variables)
            if isinstance(obj, dict):
                return obj.get(key)
            raise TypeError("仅支持 dict 下标访问")
        case ast.Tuple(elts):
            return tuple(_eval_ast_node(e, variables) for e in elts)
        case ast.List(elts):
            return [_eval_ast_node(e, variables) for e in elts]
        case ast.Compare(left, ops, comparators):
            val = _eval_ast_node(left, variables)
            for op, comp in zip(ops, comparators):
                other = _eval_ast_node(comp, variables)
                val = _apply_compare_op(val, op, other)
            return val
        case ast.BoolOp(op, values):
            vals = [_eval_ast_node(v, variables) for v in values]
            return all(vals) if isinstance(op, ast.And) else any(vals)
        case ast.UnaryOp(op, operand):
            val = _eval_ast_node(operand, variables)
            if isinstance(op, ast.Not):
                return not val
            if isinstance(op, ast.USub):
                return -val
            if isinstance(op, ast.UAdd):
                return +val
            raise ValueError(f"不支持的一元运算符: {type(op).__name__}")
        case ast.BinOp(left, op, right):
            lv = _eval_ast_node(left, variables)
            rv = _eval_ast_node(right, variables)
            return _apply_binop(lv, op, rv)
        case _:
            raise ValueError(f"不支持的操作: {type(node).__name__}")


# 模块级工具函数，不含 eval，安全求值
_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _apply_compare_op(val: Any, op: ast.cmpop, other: Any) -> Any:
    op_type = type(op)
    if op_type in _COMPARE_OPS:
        return _COMPARE_OPS[op_type](val, other)
    raise ValueError(f"不支持的比较运算符: {op_type.__name__}")


def _apply_binop(left: Any, op: ast.operator, right: Any) -> Any:
    op_type = type(op)
    if op_type in _BIN_OPS:
        return _BIN_OPS[op_type](left, right)
    raise ValueError(f"不支持的二元运算符: {op_type.__name__}")
