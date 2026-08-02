"""
控制流类 Action - Loop, IfElse, CompositeAction

使用的常用算法模式：
1. 策略模式：不同类型的步骤使用不同的执行策略
2. 迭代器模式：处理循环遍历
3. 责任链模式：步骤链式执行
4. 备忘录模式：保存/恢复执行状态
5. 模板方法模式：统一执行流程框架
"""
from app.models.execution.action_params import CompositeParams, CompositeResult
from app.models.execution.action_params import IfElseParams, IfElseResult
from app.models.execution.action_params import LoopParams, LoopResult
from app.models.execution.action_params import BaseWorkflowStep, WorkflowStep, workflow_step_adapter, _ensure_action_type
from app.models.execution.condition_models import (
    ConditionRule,
    evaluate_rule,
    evaluate_condition,
    ConditionEvaluateError,
)
import ast
import operator
import time
import re
import uuid
from typing import Dict, List, Any,  Iterator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Any
from botright.playwright_mock import Page
from loguru import logger
from app.services.execution.actions.base import BaseAction, ActionResult
from app.config import settings
from app.models.database.workflow.models import BuiltinActionType
from app.models.database.log.models import ActionLogSourceEnum
from app.services.execution.action_logger import ActionLogContext, save_action_log, resolve_log_option
from app.utils.depends.session_manager import DatabaseSessionManager


class ExecutionContext:
    """执行上下文 - 保存执行状态（备忘录模式）"""

    def __init__(self, variables: Dict = None, exec_meta: Dict | None = None):
        self.variables = dict(variables) if variables else {}
        self.execution_stack: List[str] = []
        self.current_depth = 0
        # 执行元信息由引擎透传，用于操作日志采集的链路串联
        meta = exec_meta or {}
        self.execution_id = str(meta.get("execution_id") or uuid.uuid4().hex)
        self.parent_execution_id = meta.get("parent_execution_id")
        self.browser_id = str(meta.get("browser_id") or "")
        self.session_id = str(meta.get("session_id") or "")
        self.workflow_id = meta.get("workflow_id")
        # 从父操作透传下来的日志采集配置（仅当父操作启用了采集时非空）
        self.log_config = meta.get("log_config")

    def save_state(self) -> Dict:
        """保存当前状态"""
        return {
            'variables': dict(self.variables),
            'execution_stack': list(self.execution_stack),
            'current_depth': self.current_depth
        }

    def restore_state(self, state: Dict):
        """恢复状态"""
        self.variables = state['variables']
        self.execution_stack = state['execution_stack']
        self.current_depth = state['current_depth']

    def push_stack(self, action_id: str):
        """压入执行栈"""
        self.execution_stack.append(action_id)

    def pop_stack(self):
        """弹出执行栈"""
        if self.execution_stack:
            return self.execution_stack.pop()

    def has_cycle(self, action_id: str) -> bool:
        """检测循环引用"""
        return action_id in self.execution_stack


class StepExecutor:
    """步骤执行器 - 策略模式的上下文"""

    def __init__(self, context: ExecutionContext, page: Page, mid: int):
        self.context = context
        self.page = page
        self.mid = mid

    async def execute(self, step: WorkflowStep, step_index: int = 0) -> ActionResult:
        """执行单个步骤，根据类型选择策略"""
        action_id = step.action_id

        # 检测循环引用
        if self.context.has_cycle(action_id):
            logger.warning(f"检测到循环引用: {action_id}")
            return ActionResult(
                success=False,
                error=f"检测到循环引用: {action_id}",
                action_id=action_id,
                action_name=action_id
            )

        # 获取执行策略
        strategy = self._get_strategy(action_id)
        return await strategy.execute(step, step_index)

    def _get_strategy(self, action_id: str) -> 'ExecutionStrategy':
        """根据 action_id 获取执行策略"""
        if action_id == BuiltinActionType.LOOP:
            return LoopStrategy(self)
        elif action_id == BuiltinActionType.IF_ELSE:
            return IfElseStrategy(self)
        else:
            return AtomicStrategy(self)


class ExecutionStrategy:
    """执行策略接口"""

    def __init__(self, executor: StepExecutor):
        self.executor = executor
        self.context = executor.context
        self.page = executor.page
        self.mid = executor.mid

    async def execute(self, step: WorkflowStep, step_index: int = 0) -> ActionResult:
        """执行策略"""
        raise NotImplementedError

    def _evaluate_condition(self, condition: ConditionRule | str | None) -> bool:
        """安全评估条件表达式

        - ConditionRule: 使用 evaluate_rule() 结构化评估（零 eval）
        - str: 使用 AST 递归 safe_evaluate_condition()（无 eval，用于循环条件）
        """
        if condition is None:
            return False
        if isinstance(condition, ConditionRule):
            try:
                return evaluate_rule(condition, self.context.variables)
            except ConditionEvaluateError as e:
                logger.warning(f"ConditionRule 评估失败: {e}")
                return False
        return safe_evaluate_condition(condition, self.context.variables)

    def _eval_node(self, node: ast.AST, variables: Dict | None = None) -> Any:
        """委托给模块级 _eval_ast_node"""
        if variables is None:
            variables = self.context.variables
        return _eval_ast_node(node, variables)


# 模块级安全条件评估函数，供 ConditionIterator、LoopAction 等使用
def safe_evaluate_condition(condition: str | None, variables: dict) -> bool:
    """安全评估条件表达式（AST 白名单方式，不使用 eval）

    variables 中的键可直接作为变量名引用，例如：
      element_found == True
      loop_index >= 5
    """
    if not condition:
        return False
    try:
        tree = ast.parse(condition.strip(), mode='eval')
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
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_, ast.IsNot: operator.is_not,
}

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
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


class AtomicStrategy(ExecutionStrategy):
    """原子操作执行策略"""

    async def execute(self, step: WorkflowStep, step_index: int = 0) -> ActionResult:
        """执行原子操作"""
        action_id = step.action_id
        params = step.params or {}

        # 替换模板变量
        params = self._replace_templates(params)

        # 解析生效的日志采集配置：自定义操作按其基础配置，内置操作回落服务端兜底；
        # 子步骤继承父操作透传下来的采集配置（也可被自身参数中的 log 选项覆盖）
        substep_cfg = await resolve_log_option(self.mid, action_id, params, self.context.log_config)

        # 确定日志用的 action_type（自定义操作 ca_xxx 映射为 composite）
        log_action_type = action_id if not action_id.startswith(
            'ca_') else BuiltinActionType.COMPOSITE
        log_ctx = self._new_log_context(
            action_id, params, step_index, log_action_type, log_config=substep_cfg)

        try:
            from app.services.execution.actions.all_actions import get_action_class

            # 延迟导入避免循环导入
            action_class = get_action_class(action_id)

            # 如果内置操作没找到，尝试从 DB 查找自定义操作
            if not action_class:
                from app.services.execution.action_registry import action_registry
                action_class = await action_registry.get_action_class_for_user(action_id)
                if action_class:
                    # 对于自定义复合操作，从 DB 加载 steps 到 params
                    from app.services.execution.actions.control_flow import CompositeAction as CompositeActionCls
                    if issubclass(action_class, CompositeActionCls):
                        db_steps = await action_registry.get_custom_action_steps(action_id)
                        if db_steps:
                            if not isinstance(params, dict):
                                if hasattr(params, 'model_dump'):
                                    params = params.model_dump()
                                else:
                                    params = {}
                            if not params.get("steps"):
                                params["steps"] = db_steps
                            # 校验 steps 中引用的所有 ca_ 操作是否可访问，防止越权执行
                            from app.services.execution.crud_service import action_crud_svr
                            await action_crud_svr.validate_steps_referenced_actions(
                                params["steps"] if isinstance(params, dict) else params.steps,
                                self.mid,
                            )

            if not action_class:
                raise ValueError(f"未找到操作: {action_id}")

            # 创建 action 实例
            action = action_class.new_action(
                mid=self.mid,
                page=self.page,
                params=params,
                variables=dict(self.context.variables),
                timeout=step.timeout or 30000,
                input_vars=step.input_vars or {},
                output_vars=step.output_vars or [],
            )
            # 将生效的采集配置透传给子步骤（含嵌套复合操作），供其日志采集串联
            action.exec_meta = {"log_config": substep_cfg}

            # 执行（使用 execute() 而非 _execute()，确保 _merge_output_vars 被调用）
            result = await action.execute()
            result.replaced_params = params if isinstance(params, dict) else getattr(params, 'model_dump', lambda: {})()

            # 更新变量
            if result.success:
                self._update_variables(result, step_index)

            log_ctx.action_name = str(getattr(result, "action_name", "") or action_id)
            await save_action_log(log_ctx, result)

            return result

        except Exception as e:
            logger.exception(f"执行动作失败: {action_id}")
            error_result = ActionResult(
                success=False,
                error=str(e),
                action_id=action_id,
                action_name=action_id,
                replaced_params=params if isinstance(params, dict) else getattr(params, 'model_dump', lambda: {})() or {},
            )
            await save_action_log(log_ctx, error_result)
            return error_result

    def _replace_templates(self, params: Dict) -> Dict:
        """模板变量替换（使用递归下降解析）"""
        # 若 params 是 Pydantic 模型，先转为 dict
        if not isinstance(params, dict):
            if hasattr(params, 'model_dump'):
                params = params.model_dump()
            else:
                return params

        def replace_value(value):
            if isinstance(value, str):
                # 使用正则匹配 {{变量名}} 格式
                return re.sub(
                    r"\{\{([\w.]+?)\}\}",
                    lambda m: self._get_variable_value(m.group(1)),
                    value
                )
            elif isinstance(value, dict):
                return {k: replace_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [replace_value(item) for item in value]
            return value

        return replace_value(params)

    def _get_variable_value(self, path: str) -> str:
        """获取变量值（支持点路径）"""
        parts = path.split(".")
        current = self.context.variables
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return f"{{{{{path}}}}}"
        return str(current) if current is not None else f"{{{{{path}}}}}"

    def _update_variables(self, result: ActionResult, step_index: int):
        """更新共享变量池 — 保存 result_{step_index}，并传播 last_output 和 output_vars 等变量"""
        self.context.variables[f"result_{step_index}"] = result.data
        # 传播 action.execute() 产生的变量（last_output、output_vars 映射等）
        if result.variables:
            self.context.variables.update(result.variables)

    def _new_log_context(
        self,
        action_id: str,
        params: Dict,
        depth: int,
        action_type: str | BuiltinActionType | None = None,
        log_config: Any = None,
    ) -> ActionLogContext:
        """构建操作日志采集上下文（复合操作内部的子步骤）"""
        if not isinstance(params, dict) and hasattr(params, 'model_dump'):
            params = params.model_dump()
        return ActionLogContext(
            mid=self.mid,
            action_id=action_id,
            action_name=action_id,
            action_type=str(action_type or action_id),
            source=ActionLogSourceEnum.WORKFLOW,
            execution_id=self.context.execution_id,
            parent_execution_id=getattr(self.context, "parent_execution_id", None),
            depth=depth,
            workflow_id=getattr(self.context, "workflow_id", None),
            browser_id=str(getattr(self.context, "browser_id", "") or ""),
            session_id=str(getattr(self.context, "session_id", "") or ""),
            page=self.page,
            params=params if isinstance(params, dict) else {},
            variables=dict(self.context.variables),
            log_config=log_config,
        )


class LoopStrategy(ExecutionStrategy):
    """循环执行策略（迭代器模式）"""

    @staticmethod
    def _extract_field(obj: Any, path: str) -> Any:
        """安全获取嵌套字段值，支持点分隔路径如 'loop_item.user.name'"""
        parts = path.split(".")
        current = obj
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
        return current

    def _resolve_param_mapping(
        self, params: LoopParams | Dict, loop_item_var: str, loop_index_var: str,
    ) -> dict[str, Any]:
        """解析参数映射，将循环项字段映射为目标参数值"""
        mapping = getattr(params, 'param_mapping', None) or (params.get("param_mapping") if isinstance(params, dict) else None)  # type: ignore[union-attr]
        if not mapping:
            return {}

        resolved: dict[str, Any] = {}
        for target_key, source_path in mapping.items():
            # 解析源路径：支持 loop_item.field 和 loop_index
            source_path_str = str(source_path)
            if source_path_str.startswith(f"{loop_item_var}."):
                field_path = source_path_str[len(loop_item_var) + 1:]
                item = self.context.variables.get(loop_item_var)
                resolved[target_key] = self._extract_field(item, field_path)
            elif source_path_str == loop_item_var:
                resolved[target_key] = self.context.variables.get(loop_item_var)
            elif source_path_str == loop_index_var:
                resolved[target_key] = self.context.variables.get(loop_index_var)
            else:
                # 直接按路径从 variables 中解析
                resolved[target_key] = self._extract_field(self.context.variables, source_path_str)

        return resolved

    def _apply_mapped_params(self, step: WorkflowStep, mapped: dict[str, Any]) -> WorkflowStep:
        """将映射后的参数注入到步骤参数中（浅拷贝步骤）"""
        if not mapped:
            return step

        # 获取现有 params
        raw_params = step.params or {}
        if not isinstance(raw_params, dict):
            if hasattr(raw_params, 'model_dump'):
                raw_params = raw_params.model_dump()
            else:
                raw_params = {}

        # 合并映射参数（映射参数优先级高于原始参数）
        merged = {**raw_params, **mapped}
        step.params = merged
        return step

    async def execute(self, step: WorkflowStep, step_index: int = 0) -> ActionResult:
        """执行循环操作"""
        params = step.params or {}

        # 优先从 params 获取子步骤，其次从 step.children 获取
        children = []
        if hasattr(params, 'loopBranch') and params.loopBranch:
            children = params.loopBranch
        elif step.children:
            children = step.children

        if not children:
            return ActionResult(
                success=False,
                error="循环体为空",
                action_id=BuiltinActionType.LOOP,
            )

        # 获取变量名配置
        loop_item_var = getattr(params, 'loop_item_var', None) or params.get("loop_item_var") or "loop_item"  # type: ignore[union-attr]
        loop_index_var = getattr(params, 'loop_index_var', None) or params.get("loop_index_var") or "loop_index"  # type: ignore[union-attr]

        # 获取 break/continue 条件（支持 dict 或 ConditionRule）
        break_cond = getattr(params, 'break_condition', None) or params.get("break_condition")  # type: ignore[union-attr]
        continue_cond = getattr(params, 'continue_condition', None) or params.get("continue_condition")  # type: ignore[union-attr]
        # dict 反序列化为 ConditionRule（JSON 从前端传来时是 dict）
        if isinstance(break_cond, dict):
            break_cond = ConditionRule.model_validate(break_cond)
        if isinstance(continue_cond, dict):
            continue_cond = ConditionRule.model_validate(continue_cond)

        # 创建循环迭代器
        loop_iterator = self._create_loop_iterator(params)

        results = []
        iteration = 0
        was_broken = False
        was_continued = False

        # 递归深度检查
        if self.context.current_depth >= settings.workflow_max_nesting_depth:
            return ActionResult(
                success=False,
                error=f"嵌套深度超过限制 ({self.context.current_depth}/{settings.workflow_max_nesting_depth})",
                action_id=BuiltinActionType.LOOP,
            )

        # 保存状态
        saved_state = self.context.save_state()
        self.context.current_depth += 1

        try:
            for index, item in loop_iterator:
                iteration += 1
                self.context.variables[loop_index_var] = index
                self.context.variables[loop_item_var] = item
                self.context.variables["loop_total"] = loop_iterator.total

                # 每次迭代开始前评估 break 条件
                if break_cond and self._evaluate_condition(break_cond):
                    was_broken = True
                    break

                # 每次迭代开始前评估 continue 条件
                if continue_cond and self._evaluate_condition(continue_cond):
                    was_continued = True
                    continue

                # 解析参数映射
                mapped_params = self._resolve_param_mapping(params, loop_item_var, loop_index_var)

                # 对子步骤应用参数映射
                mapped_children = [
                    self._apply_mapped_params(child, mapped_params)
                    if isinstance(child, dict) or hasattr(child, 'params')
                    else child
                    for child in children
                ]

                # 执行子步骤（带 break/continue 条件判断）
                child_results, should_break, should_continue = await self._execute_children(
                    mapped_children, iteration, break_condition=break_cond, continue_condition=continue_cond,
                )
                results.extend(child_results)

                if should_break:
                    was_broken = True
                    break
                if should_continue:
                    was_continued = True
                    continue

                # 检查子步骤是否全部失败
                if child_results and not child_results[-1].success:
                    break

        finally:
            self.context.current_depth -= 1
            self.context.restore_state(saved_state)

        return ActionResult(
            success=True,
            data=LoopResult(
                iterations=iteration, total_results=len(results),
                results=[{"action_id": r.action_id, "success": r.success} for r in results],
                was_broken=was_broken, was_continued=was_continued,
            ),
            execution_time=sum(r.execution_time for r in results),
            action_id=BuiltinActionType.LOOP,
        )

    def _create_loop_iterator(self, params: Dict) -> 'LoopIterator':
        """创建循环迭代器"""
        # 新版 loop_source 模式
        loop_source = params.get("loop_source", "fixed_count")
        loop_items_var = params.get("loop_items_var")
        loop_items_expr = params.get("loop_items_expr")
        loop_items_json = params.get("loop_items_json")

        if loop_source == "json_list" and loop_items_json:
            # 直接使用传入的 JSON 列表
            if isinstance(loop_items_json, list):
                return ListIterator(loop_items_json)
            logger.warning(f"loop_items_json 不是列表类型: {type(loop_items_json)}")
            return ListIterator([])

        if loop_source == "variable" and loop_items_var:
            # 从变量中解析 items 列表
            items = self._extract_field(self.context.variables, loop_items_var)
            if isinstance(items, list):
                return ListIterator(items)
            logger.warning(f"loop_items_var '{loop_items_var}' 解析结果不是列表: {type(items)}")
            return ListIterator([])

        if loop_source == "expression" and loop_items_expr:
            # 安全评估表达式获取 items
            try:
                import ast as _ast
                tree = _ast.parse(loop_items_expr.strip(), mode='eval')
                items = _eval_ast_node(tree.body, self.context.variables)
                if isinstance(items, list):
                    return ListIterator(items)
                logger.warning(f"loop_items_expr 解析结果不是列表: {type(items)}")
                return ListIterator([])
            except Exception as e:
                logger.warning(f"loop_items_expr 评估失败: {e}")
                return ListIterator([])

        if loop_source == "fixed_count":
            count = params.get("count", 1)
            return CountIterator(count)

        # 向后兼容旧字段
        items = params.get("items")
        loop_count = params.get("loop_count")
        loop_while = params.get("loop_while")
        loop_until = params.get("loop_until")

        if items:
            return ListIterator(items)
        elif loop_count:
            return CountIterator(loop_count)
        elif loop_while or loop_until:
            return ConditionIterator(loop_while, loop_until, self.context)
        else:
            return CountIterator(1)

    async def _execute_children(
        self, children: List[WorkflowStep], iteration: int,
        break_condition: str | None = None, continue_condition: str | None = None,
    ) -> tuple[List[ActionResult], bool, bool]:
        """执行子步骤，返回 (results, should_break, should_continue)"""
        results = []
        for i, child_step in enumerate(children):
            result: ActionResult = await self.executor.execute(child_step, iteration * 100 + i)
            results.append(result)

            # 每步执行后检查 break/continue 条件
            if break_condition and self._evaluate_condition(break_condition):
                return results, True, False
            if continue_condition and self._evaluate_condition(continue_condition):
                return results, False, True

            # 检查是否需要中断（失败且不重试）
            retry = child_step.retry if hasattr(
                child_step, 'retry') else child_step.get("retry", 0)
            if not result.success and retry == 0:
                break
        return results, False, False


class IfElseStrategy(ExecutionStrategy):
    """条件分支执行策略"""

    async def execute(self, step: WorkflowStep, step_index: int = 0) -> ActionResult:
        """执行条件分支"""
        params = step.params or {}

        # 获取条件和分支
        raw_condition = params.condition if hasattr(
            params, 'condition') else params.get("condition")
        # 兼容 dict 形式（JSON 反序列化时 params 可能是 dict）
        if isinstance(raw_condition, dict):
            condition = ConditionRule.model_validate(raw_condition)
        else:
            condition = raw_condition
        true_branch = list(params.TrueBranch) if hasattr(
            params, 'TrueBranch') and params.TrueBranch else []
        false_branch = list(params.FalseBranch) if hasattr(
            params, 'FalseBranch') and params.FalseBranch else []

        # 递归深度检查
        if self.context.current_depth >= settings.workflow_max_nesting_depth:
            return ActionResult(
                success=False,
                error=f"嵌套深度超过限制 ({self.context.current_depth}/{settings.workflow_max_nesting_depth})",
                action_id=BuiltinActionType.IF_ELSE,
            )

        # 评估条件
        condition_result = self._evaluate_condition(condition)
        self.context.variables["condition_result"] = condition_result

        # 选择分支
        selected_branch = true_branch if condition_result else false_branch
        branch_name = "true_branch" if condition_result else "false_branch"

        if not selected_branch:
            return ActionResult(
                success=True,
                data=IfElseResult(branch=branch_name, executed=False),
                action_id=BuiltinActionType.IF_ELSE,
            )

        # 保存状态并执行
        saved_state = self.context.save_state()
        self.context.current_depth += 1

        try:
            results = await self._execute_branch(selected_branch)
            last_result = results[-1] if results else None

            return ActionResult(
                success=last_result.success if last_result else True,
                data=IfElseResult(branch=branch_name, executed=True, results=[{"action_id": r.action_id, "success": r.success} for r in results]),
                execution_time=sum(r.execution_time for r in results),
                action_id=BuiltinActionType.IF_ELSE,
            )
        finally:
            self.context.current_depth -= 1
            self.context.restore_state(saved_state)

    async def _execute_branch(self, branch: List[WorkflowStep]) -> List[ActionResult]:
        """执行分支步骤"""
        results = []
        for i, step in enumerate(branch):
            result = await self.executor.execute(step, i)
            results.append(result)
            # 检查是否需要中断
            retry = step.retry if hasattr(
                step, 'retry') else step.get("retry", 0)
            if not result.success and retry == 0:
                break
        return results


# ============ 迭代器实现 ============

class LoopIterator(Iterator):
    """循环迭代器基类"""

    def __init__(self):
        self.index = 0
        self.total = 0

    def __iter__(self):
        return self

    def __next__(self) -> tuple[int, Any]:
        raise NotImplementedError


class ListIterator(LoopIterator):
    """列表迭代器"""

    def __init__(self, items: List[Any]):
        super().__init__()
        self.items = items
        self.total = len(items)

    def __next__(self) -> tuple[int, Any]:
        if self.index >= self.total:
            raise StopIteration
        item = self.items[self.index]
        current_index = self.index
        self.index += 1
        return (current_index, item)


class CountIterator(LoopIterator):
    """计数迭代器"""

    def __init__(self, count: int):
        super().__init__()
        self.count = count
        self.total = count

    def __next__(self) -> tuple[int, Any]:
        if self.index >= self.count:
            raise StopIteration
        current_index = self.index
        self.index += 1
        return (current_index, None)


class ConditionIterator(LoopIterator):
    """条件迭代器"""

    def __init__(self, while_expr: str, until_expr: str, context: ExecutionContext):
        super().__init__()
        self.while_expr = while_expr
        self.until_expr = until_expr
        self.context = context
        self.max_iterations = 100

    def __next__(self) -> tuple[int, Any]:
        if self.index >= self.max_iterations:
            raise StopIteration

        # 检查 while 条件
        if self.while_expr:
            try:
                if not safe_evaluate_condition(self.while_expr, self.context.variables):
                    raise StopIteration
            except Exception as e:
                logger.warning(f"loop_while 评估失败: {e}")
                raise StopIteration

        # 检查 until 条件
        if self.until_expr:
            try:
                if safe_evaluate_condition(self.until_expr, self.context.variables):
                    raise StopIteration
            except Exception as e:
                logger.warning(f"loop_until 评估失败: {e}")

        current_index = self.index
        self.index += 1
        self.total = self.index  # 动态更新
        return (current_index, None)


# ============ Action 类 ============

class LoopAction(BaseAction[LoopParams]):
    """循环控制流操作"""
    action_id: BuiltinActionType = BuiltinActionType.LOOP
    action_type: BuiltinActionType = BuiltinActionType.LOOP
    params: LoopParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: LoopParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    def _merge_output_vars(self, action_result: ActionResult) -> None:
        """
        循环操作的特殊变量合并：
        - 处理 LoopResult 字段到 output_vars 的映射
        - 不设置 last_output（子步骤已在 _execute() 中设置）
        """
        data = action_result.data
        if data is None:
            return

        if isinstance(data, dict):
            data_dict = data
        elif hasattr(data, 'model_dump'):
            data_dict = data.model_dump()
        else:
            return

        if self.output_vars:
            data_values = list(data_dict.values())
            for i, var_name in enumerate(self.output_vars):
                if i < len(data_values):
                    self.variables[var_name] = data_values[i]

    async def _execute(self) -> ActionResult[LoopResult]:
        """执行循环 - 支持 loop_source (fixed_count/variable/expression) + param_mapping"""
        start_time = time.time()

        # 参数验证
        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.action_id, action_name=self.action_name,
            )

        # 获取子步骤（从 params 中获取）
        children = self.params.loopBranch
        if not children:
            return ActionResult(success=True, data=LoopResult(message="无子步骤可执行"), action_id=self.action_id, action_name=self.action_name)

        # 获取变量名配置
        loop_item_var = getattr(self.params, 'loop_item_var', 'loop_item') or 'loop_item'
        loop_index_var = getattr(self.params, 'loop_index_var', 'loop_index') or 'loop_index'

        # 创建执行上下文和执行器
        context = ExecutionContext(self.variables, self.exec_meta)
        executor = StepExecutor(context, self.page, self.mid)

        # 解析 param_mapping
        param_mapping: dict[str, str] = getattr(self.params, 'param_mapping', None) or {}

        # 获取循环参数
        loop_source = getattr(self.params, 'loop_source', 'fixed_count') or 'fixed_count'
        count = getattr(self.params, 'count', 1) or 1
        loop_items_var = getattr(self.params, 'loop_items_var', None)
        loop_items_expr = getattr(self.params, 'loop_items_expr', None)
        loop_items_json = getattr(self.params, 'loop_items_json', None)

        # 获取 break/continue 条件（支持 dict 或 ConditionRule）
        break_cond_raw = getattr(self.params, 'break_condition', None)
        continue_cond_raw = getattr(self.params, 'continue_condition', None)
        # dict 反序列化为 ConditionRule（JSON 从前端传来时是 dict）
        if isinstance(break_cond_raw, dict):
            break_cond_raw = ConditionRule.model_validate(break_cond_raw)
        if isinstance(continue_cond_raw, dict):
            continue_cond_raw = ConditionRule.model_validate(continue_cond_raw)

        # 向后兼容旧参数
        loop_count = getattr(self.params, 'loop_count', None)
        loop_while = getattr(self.params, 'loop_while', None)
        loop_until_val = getattr(self.params, 'loop_until', None)

        results: list[dict] = []

        if loop_source == "json_list" and loop_items_json:
            # 直接使用传入的 JSON 列表
            items = loop_items_json if isinstance(loop_items_json, list) else []
            for i, item_value in enumerate(items):
                context.variables[loop_index_var] = i
                context.variables[loop_item_var] = item_value
                # 每次迭代开始前评估 break 条件
                if break_cond_raw and evaluate_rule(break_cond_raw, context.variables):
                    break
                # 每次迭代开始前评估 continue 条件
                if continue_cond_raw and evaluate_rule(continue_cond_raw, context.variables):
                    continue
                mapped = self._resolve_param_mapping_static(param_mapping, item_value, context.variables, loop_item_var, loop_index_var)
                mapped_children = self._inject_params_to_children(children, mapped)
                child_results = await self._execute_steps_with_context(executor, mapped_children)
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break

        elif loop_source == "variable" and loop_items_var:
            # 从变量获取 items 列表
            items = self._resolve_items_from_variable(context, loop_items_var)
            for i, item_value in enumerate(items):
                context.variables[loop_index_var] = i
                context.variables[loop_item_var] = item_value
                # 每次迭代开始前评估 break 条件
                if break_cond_raw and evaluate_rule(break_cond_raw, context.variables):
                    break
                # 每次迭代开始前评估 continue 条件
                if continue_cond_raw and evaluate_rule(continue_cond_raw, context.variables):
                    continue
                mapped = self._resolve_param_mapping_static(param_mapping, item_value, context.variables, loop_item_var, loop_index_var)
                mapped_children = self._inject_params_to_children(children, mapped)
                child_results = await self._execute_steps_with_context(executor, mapped_children)
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break

        elif loop_source == "expression" and loop_items_expr:
            # 表达式计算 items
            items = self._resolve_items_from_expr(context, loop_items_expr)
            for i, item_value in enumerate(items):
                context.variables[loop_index_var] = i
                context.variables[loop_item_var] = item_value
                # 每次迭代开始前评估 break 条件
                if break_cond_raw and evaluate_rule(break_cond_raw, context.variables):
                    break
                # 每次迭代开始前评估 continue 条件
                if continue_cond_raw and evaluate_rule(continue_cond_raw, context.variables):
                    continue
                mapped = self._resolve_param_mapping_static(param_mapping, item_value, context.variables, loop_item_var, loop_index_var)
                mapped_children = self._inject_params_to_children(children, mapped)
                child_results = await self._execute_steps_with_context(executor, mapped_children)
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break

        elif loop_count is not None and loop_count > 0:
            # 固定次数循环
            for i in range(loop_count):
                context.variables[loop_index_var] = i
                context.variables[loop_item_var] = None
                # 每次迭代开始前评估 break 条件
                if break_cond_raw and evaluate_rule(break_cond_raw, context.variables):
                    break
                # 每次迭代开始前评估 continue 条件
                if continue_cond_raw and evaluate_rule(continue_cond_raw, context.variables):
                    continue
                mapped = self._resolve_param_mapping_static(param_mapping, None, context.variables, loop_item_var, loop_index_var)
                mapped_children = self._inject_params_to_children(children, mapped)
                child_results = await self._execute_children_with_condition(
                    executor, mapped_children, context,
                    loop_while=loop_while, loop_until=loop_until_val,
                )
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break

        elif count > 0:
            # 新版固定次数
            for i in range(count):
                context.variables[loop_index_var] = i
                context.variables[loop_item_var] = None
                # 每次迭代开始前评估 break 条件
                if break_cond_raw and evaluate_rule(break_cond_raw, context.variables):
                    break
                # 每次迭代开始前评估 continue 条件
                if continue_cond_raw and evaluate_rule(continue_cond_raw, context.variables):
                    continue
                mapped = self._resolve_param_mapping_static(param_mapping, None, context.variables, loop_item_var, loop_index_var)
                mapped_children = self._inject_params_to_children(children, mapped)
                child_results = await self._execute_children_with_condition(
                    executor, mapped_children, context,
                    loop_while=loop_while, loop_until=loop_until_val,
                )
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break

        elif loop_while:
            # while 条件循环
            loop_i = 0
            while safe_evaluate_condition(loop_while, context.variables):
                context.variables[loop_index_var] = loop_i
                context.variables[loop_item_var] = None
                child_results = await self._execute_children_with_condition(
                    executor, children, context,
                    loop_while=loop_while, loop_until=loop_until_val,
                )
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break
                if not safe_evaluate_condition(loop_while, context.variables):
                    break
                loop_i += 1

        elif loop_until_val:
            # until 条件循环
            loop_i = 0
            while True:
                context.variables[loop_index_var] = loop_i
                context.variables[loop_item_var] = None
                child_results = await self._execute_children_with_condition(
                    executor, children, context,
                    loop_while=loop_while, loop_until=loop_until_val,
                )
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break
                if safe_evaluate_condition(loop_until_val, context.variables):
                    break
                loop_i += 1

        else:
            # 默认：执行一次
            results = await self._execute_steps_with_context(executor, children)

        # 将子步骤产生的变量回写到 self.variables
        self.variables.update(context.variables)

        # 将最后一个有数据的子步骤的 raw 返回值设为 last_output
        last_output = None
        for r_dict in reversed(results):
            step_results = r_dict.get("results", [])
            for r in reversed(step_results):
                if r.success and r.data is not None:
                    last_output = r.data
                    break
            if last_output is not None:
                break
        self.variables['last_output'] = last_output

        return ActionResult(
            success=True,
            data=LoopResult(iterations=len(results), details=results),
            execution_time=time.time() - start_time,
            action_id=self.action_id, action_name=self.action_name,
        )

    @staticmethod
    def _extract_field(obj: Any, path: str) -> Any:
        """安全获取嵌套字段值，支持点分隔路径"""
        parts = path.split(".")
        current = obj
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
        return current

    @staticmethod
    def _resolve_param_mapping_static(
        mapping: dict[str, str] | None,
        item_value: Any,
        variables: dict,
        loop_item_var: str = "loop_item",
        loop_index_var: str = "loop_index",
    ) -> dict[str, Any]:
        """解析参数映射（静态方法，供 LoopAction 使用）"""
        if not mapping:
            return {}
        resolved: dict[str, Any] = {}
        for target_key, source_path in mapping.items():
            source_path_str = str(source_path)
            if source_path_str.startswith(f"{loop_item_var}."):
                field_path = source_path_str[len(loop_item_var) + 1:]
                resolved[target_key] = LoopAction._extract_field(item_value, field_path)
            elif source_path_str == loop_item_var:
                resolved[target_key] = item_value
            elif source_path_str == loop_index_var:
                resolved[target_key] = variables.get(loop_index_var)
            else:
                resolved[target_key] = LoopAction._extract_field(variables, source_path_str)
        return resolved

    @staticmethod
    def _inject_params_to_children(
        children: list[WorkflowStep], mapped: dict[str, Any],
    ) -> list[WorkflowStep]:
        """将映射参数注入到子步骤中"""
        if not mapped or not children:
            return children
        new_children = []
        for child in children:
            raw = child.params or {}
            if hasattr(raw, 'model_dump'):
                raw = raw.model_dump()
            elif not isinstance(raw, dict):
                raw = {}
            merged = {**raw, **mapped}
            child.params = merged
            new_children.append(child)
        return new_children

    def _resolve_items_from_variable(self, context: ExecutionContext, var_ref: str) -> list:
        """从变量中解析 items 列表"""
        items = self._extract_field(context.variables, var_ref)
        if isinstance(items, list):
            return items
        logger.warning(f"loop_items_var '{var_ref}' 解析结果不是列表: {type(items)}")
        return []

    def _resolve_items_from_expr(self, context: ExecutionContext, expr: str) -> list:
        """从表达式安全解析 items 列表"""
        try:
            import ast as _ast
            tree = _ast.parse(expr.strip(), mode='eval')
            items = _eval_ast_node(tree.body, context.variables)
            if isinstance(items, list):
                return items
            logger.warning(f"loop_items_expr 解析结果不是列表: {type(items)}")
        except Exception as e:
            logger.warning(f"loop_items_expr 评估失败: {e}")
        return []

    async def _execute_children_with_condition(
        self,
        executor: StepExecutor,
        steps: list[WorkflowStep],
        context: ExecutionContext,
        loop_while: str | None = None,
        loop_until: str | None = None,
    ) -> list[dict]:
        """执行循环体内的所有步骤，每步执行前后检查 loop_while/loop_until 条件"""
        results = []
        for i, step in enumerate(steps):
            # 每步执行前检查条件
            if loop_while and not safe_evaluate_condition(loop_while, context.variables):
                break
            if loop_until and safe_evaluate_condition(loop_until, context.variables):
                break

            result = await executor.execute(step, i)
            results.append({
                "iteration": i,
                "success": result.success,
                "results": [result]
            })

            # 每步执行后检查条件
            if loop_until and safe_evaluate_condition(loop_until, context.variables):
                break
        return results

    async def _execute_steps_with_context(self, executor: StepExecutor, steps: list[WorkflowStep]) -> list[dict]:
        """使用上下文执行步骤列表"""
        results = []
        for i, step in enumerate(steps):
            result = await executor.execute(step, i)
            iteration_success = result.success
            results.append({
                "iteration": i,
                "success": iteration_success,
                "results": [result]
            })
        return results


class IfElseAction(BaseAction[IfElseParams]):
    """条件分支控制流操作"""
    action_id: BuiltinActionType = BuiltinActionType.IF_ELSE
    action_type: BuiltinActionType = BuiltinActionType.IF_ELSE
    params: IfElseParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: IfElseParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    def _merge_output_vars(self, action_result: ActionResult) -> None:
        """
        条件分支操作的特殊变量合并：
        - 处理 IfElseResult 字段到 output_vars 的映射
        - 不设置 last_output（子步骤已在 _execute() 中设置）
        """
        data = action_result.data
        if data is None:
            return

        if isinstance(data, dict):
            data_dict = data
        elif hasattr(data, 'model_dump'):
            data_dict = data.model_dump()
        else:
            return

        if self.output_vars:
            data_values = list(data_dict.values())
            for i, var_name in enumerate(self.output_vars):
                if i < len(data_values):
                    self.variables[var_name] = data_values[i]

    async def _execute(self) -> ActionResult[IfElseResult]:
        """执行条件分支 - 委托给策略执行器"""
        start_time = time.time()

        # 参数验证
        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.action_id, action_name=self.action_name,
            )

        # 获取分支步骤（从 params 中获取）
        true_branch = list(
            self.params.TrueBranch) if self.params.TrueBranch else []
        false_branch = list(
            self.params.FalseBranch) if self.params.FalseBranch else []

        # 设置参数到分支步骤
        for branch in [true_branch, false_branch]:
            for step in branch:
                if step.params is None:
                    step.params = {}

        # 创建执行上下文和执行器
        context = ExecutionContext(self.variables, self.exec_meta)
        executor = StepExecutor(context, self.page, self.mid)

        # 评估条件
        condition_result = self._evaluate_condition(validated_params.condition)
        branch_key = "true_branch" if condition_result else "false_branch"
        selected_branch = true_branch if condition_result else false_branch

        if not selected_branch:
            return ActionResult(
                success=True,
                data=IfElseResult(branch_taken=branch_key, message="分支无步骤"),
                execution_time=time.time() - start_time,
                action_id=self.action_id, action_name=self.action_name,
            )

        # 执行分支
        results = await self._execute_steps_with_context(executor, selected_branch)

        # 将子步骤产生的变量（last_output、output_vars 映射等）回写到 self.variables
        self.variables.update(context.variables)

        # 将最后一个有数据的子步骤的 raw 返回值设为 last_output
        last_output = None
        for r_dict in reversed(results):
            if r_dict.get("success") and r_dict.get("data") is not None:
                last_output = r_dict["data"]
                break
        self.variables['last_output'] = last_output

        return ActionResult(
            success=True,
            data=IfElseResult(branch_taken=branch_key, results=results),
            execution_time=time.time() - start_time,
            action_id=self.action_id, action_name=self.action_name,
        )

    def _evaluate_condition(self, condition: ConditionRule) -> bool:
        """评估条件规则（纯 Python 逻辑，不使用 eval）"""
        try:
            return evaluate_rule(condition, self.variables)
        except ConditionEvaluateError as e:
            logger.warning(f"IfElseAction 条件评估失败: {e}")
            return False

    async def _execute_steps_with_context(self, executor: StepExecutor, steps: List[WorkflowStep]) -> List[dict]:
        """使用上下文执行步骤列表"""
        results = []
        for step in steps:
            result = await executor.execute(step)
            results.append({
                "action_id": result.action_id,
                "success": result.success,
                "data": result.data
            })
        return results


class CompositeAction(BaseAction[CompositeParams]):
    """组合动作基类 - 使用责任链模式执行步骤"""
    action_id: BuiltinActionType = BuiltinActionType.COMPOSITE
    action_type: BuiltinActionType = BuiltinActionType.COMPOSITE
    params: CompositeParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: CompositeParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    @property
    def steps(self):
        if self.params and hasattr(self.params, 'steps'):
            return self.params.steps
        return None

    def _merge_output_vars(self, action_result: ActionResult) -> None:
        """
        复合操作的特殊变量合并：
        - 处理 CompositeResult 字段到 output_vars 的映射
        - 不设置 last_output（子步骤已在 _execute() 中设置）
        """
        data = action_result.data
        if data is None:
            return

        # 将 data 转为 dict
        if isinstance(data, dict):
            data_dict = data
        elif hasattr(data, 'model_dump'):
            data_dict = data.model_dump()
        else:
            return  # 非 dict/模型，不做处理

        # 处理 output_vars 映射（从 CompositeResult 字段）
        if self.output_vars:
            data_values = list(data_dict.values())
            for i, var_name in enumerate(self.output_vars):
                if i < len(data_values):
                    self.variables[var_name] = data_values[i]

        # 注意：不设置 last_output，子步骤已在 _execute() 中设置了最后一个步骤的 raw 返回值

    async def _execute(self) -> ActionResult:
        """执行组合动作 - 责任链模式"""
        start_time = time.time()

        # 统一将 steps 从 params 中提取
        raw_steps = None
        if self.params:
            if hasattr(self.params, 'steps'):
                raw_steps = self.params.steps
            elif isinstance(self.params, dict):
                raw_steps = self.params.get('steps')
        if not raw_steps:
            return ActionResult(
                success=False,
                error="组合动作没有步骤",
                execution_time=0,
                action_id=self.action_id,
                action_name=self.action_name,
            )

        # 统一转换为 WorkflowStep 对象
        steps: List[WorkflowStep] = []
        for s in raw_steps:
            if isinstance(s, BaseWorkflowStep):
                steps.append(s)
            elif isinstance(s, dict):
                try:
                    steps.append(workflow_step_adapter.validate_python(
                        _ensure_action_type(s)))
                except Exception as e:
                    return ActionResult(
                        success=False,
                        error=f"步骤格式验证失败: {str(e)}",
                        execution_time=time.time() - start_time,
                        action_id=self.action_id,
                        action_name=self.action_name,
                    )

        # 创建执行上下文和执行器
        context = ExecutionContext(self.variables, self.exec_meta)
        executor = StepExecutor(context, self.page, self.mid)

        # 执行所有步骤（责任链模式）
        results = await self._execute_chain(executor, steps)

        # 将子步骤产生的变量（last_output、output_vars 映射等）回写到 self.variables
        self.variables.update(context.variables)

        # 将最后一个有数据的子步骤的 raw 返回值设为 last_output
        last_output = None
        for r in reversed(results):
            if r.success and r.data is not None:
                last_output = r.data
                break
        self.variables['last_output'] = last_output

        total = len(results)
        success_count = sum(1 for r in results if r.success)
        all_success = all(r.success for r in results)

        return ActionResult(
            success=all_success,
            data=CompositeResult(total_steps=total, success_count=success_count, results=[{"action_id": r.action_id, "action_name": r.action_name, "success": r.success, "error": r.error, "execution_time": r.execution_time} for r in results]),
            error=results[-1].error if results and not all_success else None,
            execution_time=time.time() - start_time,
            action_id=self.action_id,
            action_name=self.action_name,
            logs=self.get_logs(),
        )

    async def _execute_chain(self, executor: StepExecutor, steps: List[WorkflowStep]) -> List[ActionResult]:
        """责任链模式执行步骤列表"""
        results = []

        for i, step in enumerate(steps):
            # 执行步骤
            result = await executor.execute(step, i)
            results.append(result)

            # 错误处理：retry 重试
            if not result.success and step.retry > 0:
                for _ in range(step.retry):
                    result = await executor.execute(step, i)
                    results.append(result)
                    if result.success:
                        break

            # 步骤失败且重试也失败时，中断后续步骤
            if not result.success and step.retry == 0:
                break
            elif not result.success:
                # 检查所有重试结果，如果都失败则中断
                retry_results = results[-(step.retry + 1):]
                if not any(r.success for r in retry_results):
                    break

        return results
