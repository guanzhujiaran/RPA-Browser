"""
控制流类 Action - Loop, IfElse, CompositeAction

使用的常用算法模式：
1. 策略模式：不同类型的步骤使用不同的执行策略
2. 迭代器模式：处理循环遍历
3. 责任链模式：步骤链式执行
4. 备忘录模式：保存/恢复执行状态
5. 模板方法模式：统一执行流程框架
"""
from app.models.execution.action_params import CompositeParams
from app.models.execution.action_params import IfElseParams
from app.models.execution.action_params import LoopParams
from app.models.execution.action_params import BaseWorkflowStep, WorkflowStep, workflow_step_adapter, _ensure_action_type
import time
import re
import uuid
from datetime import datetime
from typing import Dict, List, Any,  Iterator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Any
from botright.playwright_mock import Page
from loguru import logger
from app.services.execution.actions.base import BaseAction, ActionResult
from app.config import settings
from app.models.database.workflow.models import (
    BuiltinActionType, ExecutionStatus,
    ActionExecutionLog
)
from app.utils.depends.session_manager import DatabaseSessionManager


class ExecutionContext:
    """执行上下文 - 保存执行状态（备忘录模式）"""

    def __init__(self, variables: Dict[str, Any] = None):
        self.variables = dict(variables) if variables else {}
        self.execution_stack: List[str] = []
        self.current_depth = 0
        self.execution_id = str(uuid.uuid4())

    def save_state(self) -> Dict[str, Any]:
        """保存当前状态"""
        return {
            'variables': dict(self.variables),
            'execution_stack': list(self.execution_stack),
            'current_depth': self.current_depth
        }

    def restore_state(self, state: Dict[str, Any]):
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


class AtomicStrategy(ExecutionStrategy):
    """原子操作执行策略"""

    async def execute(self, step: WorkflowStep, step_index: int = 0) -> ActionResult:
        """执行原子操作"""
        action_id = step.action_id
        params = step.params or {}

        # 替换模板变量
        params = self._replace_templates(params)

        # 确定日志用的 action_type（自定义操作 ca_xxx 映射为 composite）
        log_action_type = action_id if not action_id.startswith('ca_') else BuiltinActionType.COMPOSITE

        # 记录执行开始
        await self._log_execution(action_id, params, step_index, ExecutionStatus.RUNNING, log_action_type)

        try:
            from app.services.execution.actions.all_actions import get_action_class

            # 延迟导入避免循环导入
            action_class = get_action_class(action_id)

            # 如果内置操作没找到，尝试从 DB 查找自定义操作
            if not action_class:
                from app.services.execution.action_registry import action_registry
                action_class = await action_registry.get_action_class_for_user(action_id, self.mid)
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

            # 执行
            result = await action.execute()

            # 更新变量
            if result.success:
                self._update_variables(result, step_index)

            # 记录执行完成
            await self._log_execution_complete(
                action_id,
                ExecutionStatus.SUCCESS if result.success else ExecutionStatus.FAILED,
                result
            )

            return result

        except Exception as e:
            logger.exception(f"执行动作失败: {action_id}")
            error_result = ActionResult(
                success=False,
                error=str(e),
                action_id=action_id,
                action_name=action_id,
            )
            await self._log_execution_complete(action_id, ExecutionStatus.FAILED, error_result)
            return error_result

    def _replace_templates(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """模板变量替换（使用递归下降解析）"""
        # 若 params 是 Pydantic 模型，先转为 dict
        if not isinstance(params, dict):
            if hasattr(params, 'model_dump'):
                params = params.model_dump()
            else:
                return params

        def replace_value(value: Any) -> Any:
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
        """更新共享变量池"""
        if hasattr(result, 'data') and isinstance(result.data, dict):
            for key, value in result.data.items():
                self.context.variables[key] = value
        self.context.variables[f"result_{step_index}"] = result.data

    async def _log_execution(self, action_id: str, params: Dict[str, Any], depth: int, status: ExecutionStatus, action_type: str | BuiltinActionType | None = None):
        """记录执行日志"""
        # 若 params 是 Pydantic 模型，先转为 dict 以避免 JSON 序列化错误
        if not isinstance(params, dict) and hasattr(params, 'model_dump'):
            params = params.model_dump()
        async with DatabaseSessionManager.async_session() as session:
            log = ActionExecutionLog(
                execution_id=self.context.execution_id,
                action_id=action_id,
                action_name=action_id,
                action_type=action_type or action_id,
                status=status,
                params=params,
                depth=depth,
                mid=self.mid,
            )
            session.add(log)
            await session.commit()

    async def _log_execution_complete(self, action_id: str, status: ExecutionStatus, result: ActionResult):
        """更新执行完成日志"""
        async with DatabaseSessionManager.async_session() as session:
            query = select(ActionExecutionLog).where(
                ActionExecutionLog.execution_id == self.context.execution_id,
                ActionExecutionLog.action_id == action_id,
                ActionExecutionLog.finished_at == None,
            )
            result_row = await session.exec(query)
            log = result_row.scalars().first()
            if log:
                log.status = status
                log.result_data = {
                    "success": result.success, "data": result.data}
                log.error_message = result.error
                log.execution_time = result.execution_time
                log.finished_at = datetime.now()
                await session.commit()


class LoopStrategy(ExecutionStrategy):
    """循环执行策略（迭代器模式）"""

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

        # 创建循环迭代器
        loop_iterator = self._create_loop_iterator(params)

        results = []
        iteration = 0

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
                self.context.variables["loop_index"] = index
                self.context.variables["loop_item"] = item
                self.context.variables["loop_total"] = loop_iterator.total

                # 执行子步骤
                child_results = await self._execute_children(children, iteration)
                results.extend(child_results)

                # 检查是否需要中断
                if child_results and not child_results[-1].success:
                    break

        finally:
            self.context.current_depth -= 1
            self.context.restore_state(saved_state)

        return ActionResult(
            success=True,
            data={
                "iterations": iteration,
                "total_results": len(results),
                "results": [{"action_id": r.action_id, "success": r.success} for r in results]
            },
            execution_time=sum(r.execution_time for r in results),
            action_id=BuiltinActionType.LOOP,
        )

    def _create_loop_iterator(self, params: Dict[str, Any]) -> 'LoopIterator':
        """创建循环迭代器"""
        loop_count = params.get("loop_count")
        loop_while = params.get("loop_while")
        loop_until = params.get("loop_until")
        items = params.get("items")

        if items:
            return ListIterator(items)
        elif loop_count:
            return CountIterator(loop_count)
        elif loop_while or loop_until:
            return ConditionIterator(loop_while, loop_until, self.context)
        else:
            return CountIterator(1)

    async def _execute_children(self, children: List[WorkflowStep], iteration: int) -> List[ActionResult]:
        """执行子步骤"""
        results = []
        for i, child_step in enumerate(children):
            result = await self.executor.execute(child_step, iteration * 100 + i)
            results.append(result)
            # 检查是否需要中断
            retry = child_step.retry if hasattr(
                child_step, 'retry') else child_step.get("retry", 0)
            if not result.success and retry == 0:
                break
        return results


class IfElseStrategy(ExecutionStrategy):
    """条件分支执行策略"""

    async def execute(self, step: WorkflowStep, step_index: int = 0) -> ActionResult:
        """执行条件分支"""
        params = step.params or {}

        # 获取条件和分支
        condition = params.condition if hasattr(
            params, 'condition') else params.get("condition")
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
                data={"branch": branch_name, "executed": False},
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
                data={
                    "branch": branch_name,
                    "executed": True,
                    "results": [{"action_id": r.action_id, "success": r.success} for r in results]
                },
                execution_time=sum(r.execution_time for r in results),
                action_id=BuiltinActionType.IF_ELSE,
            )
        finally:
            self.context.current_depth -= 1
            self.context.restore_state(saved_state)

    def _evaluate_condition(self, condition: str) -> bool:
        """安全评估条件表达式"""
        if not condition:
            return False

        try:
            # 使用安全的 eval，限制可用变量
            return eval(condition, {"__builtins__": {}}, {"state": self.context.variables})
        except Exception as e:
            logger.warning(f"条件评估失败: {e}")
            return False

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
                if not eval(self.while_expr, {}, {"state": self.context.variables}):
                    raise StopIteration
            except Exception as e:
                logger.warning(f"loop_while 评估失败: {e}")
                raise StopIteration

        # 检查 until 条件
        if self.until_expr:
            try:
                if eval(self.until_expr, {}, {"state": self.context.variables}):
                    raise StopIteration
            except Exception as e:
                logger.warning(f"loop_until 评估失败: {e}")

        current_index = self.index
        self.index += 1
        self.total = self.index  # 动态更新
        return (current_index, None)


# ============ Action 类 ============

class LoopAction(BaseAction):
    """循环控制流操作"""
    action_id: BuiltinActionType = BuiltinActionType.LOOP
    params: LoopParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict[str, Any], params: LoopParams | None = None, timeout: int = 30000, input_vars: Dict[str, Any] | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        return super().new_action(
            mid=mid, page=page, variables=variables,
            params=params, timeout=timeout,
            input_vars=input_vars, output_vars=output_vars,
            action_name=action_name,
        )

    async def execute(self) -> ActionResult:
        """执行循环 - 委托给策略执行器"""
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
            return ActionResult(success=True, data={"message": "无子步骤可执行"})

        # 创建执行上下文和执行器
        context = ExecutionContext(self.variables)
        executor = StepExecutor(context, self.page, self.mid)

        # 执行
        results = await self._execute_steps_with_context(executor, children)

        return ActionResult(
            success=True,
            data={
                "iterations": len(results),
                "details": results
            },
            execution_time=time.time() - start_time,
            action_id=self.action_id, action_name=self.action_name,
        )

    async def _execute_steps_with_context(self, executor: StepExecutor, steps: List[WorkflowStep]) -> List[dict]:
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


class IfElseAction(BaseAction):
    """条件分支控制流操作"""
    action_id: BuiltinActionType = BuiltinActionType.IF_ELSE
    params: IfElseParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict[str, Any], params: IfElseParams | None = None, timeout: int = 30000, input_vars: Dict[str, Any] | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        return super().new_action(
            mid=mid, page=page, variables=variables,
            params=params, timeout=timeout,
            input_vars=input_vars, output_vars=output_vars,
            action_name=action_name,
        )

    async def execute(self) -> ActionResult:
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
        context = ExecutionContext(self.variables)
        executor = StepExecutor(context, self.page, self.mid)

        # 评估条件
        condition_result = self._evaluate_condition(validated_params.condition)
        branch_key = "true_branch" if condition_result else "false_branch"
        selected_branch = true_branch if condition_result else false_branch

        if not selected_branch:
            return ActionResult(
                success=True,
                data={"branch_taken": branch_key, "message": "分支无步骤"},
                execution_time=time.time() - start_time,
                action_id=self.action_id, action_name=self.action_name,
            )

        # 执行分支
        results = await self._execute_steps_with_context(executor, selected_branch)

        return ActionResult(
            success=True,
            data={"branch_taken": branch_key, "results": results},
            execution_time=time.time() - start_time,
            action_id=self.action_id, action_name=self.action_name,
        )

    def _evaluate_condition(self, condition: str) -> bool:
        """评估条件"""
        state = self.variables.get("state", {})
        try:
            return eval(condition, {"__builtins__": {}}, {"state": state})
        except:
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


class CompositeAction(BaseAction):
    """组合动作基类 - 使用责任链模式执行步骤"""
    action_id: BuiltinActionType = BuiltinActionType.COMPOSITE
    params: CompositeParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict[str, Any], params: CompositeParams | None = None, timeout: int = 30000, input_vars: Dict[str, Any] | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        return super().new_action(
            mid=mid, page=page, variables=variables,
            params=params, timeout=timeout,
            input_vars=input_vars, output_vars=output_vars,
            action_name=action_name,
        )

    @property
    def steps(self):
        if self.params and hasattr(self.params, 'steps'):
            return self.params.steps
        return None

    async def execute(self) -> ActionResult:
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
                    steps.append(workflow_step_adapter.validate_python(_ensure_action_type(s)))
                except Exception as e:
                    return ActionResult(
                        success=False,
                        error=f"步骤格式验证失败: {str(e)}",
                        execution_time=time.time() - start_time,
                        action_id=self.action_id,
                        action_name=self.action_name,
                    )

        # 创建执行上下文和执行器
        context = ExecutionContext(self.variables)
        executor = StepExecutor(context, self.page, self.mid)

        # 执行所有步骤（责任链模式）
        results = await self._execute_chain(executor, steps)

        total = len(results)
        success_count = sum(1 for r in results if r.success)
        all_success = all(r.success for r in results)

        return ActionResult(
            success=all_success,
            data={
                "total_steps": total,
                "success_count": success_count,
                "results": [{"action_id": r.action_id, "success": r.success} for r in results]
            },
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
