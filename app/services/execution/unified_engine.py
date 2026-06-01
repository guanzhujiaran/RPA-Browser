"""
Unified Execution Engine - 统一执行引擎

核心设计理念：
1. 统一处理：action 和 plugin 在执行时统一判断处理
2. DP 算法：使用动态规划算法展开操作链
3. 执行追踪：记录每个操作和插件的执行记录
4. 生命周期钩子：支持 before_action, after_action 等钩子

DP 算法设计：
1. 构建执行图：根据 steps 构建依赖图
2. 拓扑排序：确定执行顺序
3. 动态展开：按顺序执行每个节点
4. 结果传递：将结果传递给下游节点
"""

import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from collections import defaultdict

from loguru import logger
from sqlmodel import select

from app.models.database.workflow.unified_models import (
    ActionResult,
    ActionContext,
    ActionCategory,
    ExecutionStatus,
    HookType,
    ExecutionRecord,
    ActionExecutionLog,
    WorkflowExecutionSession,
    Step,
    StepType,
    StepGroup,
    StepMetadata,
    LoopConfig,
    ConditionalConfig,
)
from app.services.execution.actions.base import BaseAction, ExecutionNode
from app.utils.depends.session_manager import DatabaseSessionManager


@dataclass
class ExecutionPlan:
    """执行计划"""
    execution_id: str
    nodes: List[ExecutionNode]
    execution_order: List[int]
    dependency_graph: Dict[int, List[int]]


class UnifiedExecutionEngine:
    """
    统一执行引擎

    处理所有 action 和 plugin 的执行。
    """

    def __init__(self):
        self._max_depth = 50
        self._execution_cache: Dict[str, ActionResult] = {}

    async def execute_step_group(
        self,
        step_group: StepGroup,
        ctx: ActionContext,
        registry,
        mid: str,
        execution_id: str,
    ) -> List[ActionResult]:
        """
        执行步骤组（新格式，支持前向引用）

        Args:
            step_group: 步骤组
            ctx: 执行上下文
            registry: 动作注册表
            mid: 用户ID
            execution_id: 执行ID

        Returns:
            执行结果列表
        """
        results: List[ActionResult] = []
        step_results: Dict[str, ActionResult] = {}

        # 构建执行顺序
        execution_order = step_group.get_execution_order()

        # 从入口开始
        entry_step = step_group.get_entry_step()
        if not entry_step:
            logger.warning("步骤组没有入口步骤")
            return results

        # 执行
        for step_id in execution_order:
            step = step_group.get_step(step_id)
            if not step:
                continue

            # 记录执行开始
            await self._log_execution(
                execution_id=execution_id,
                action_id=f"{step_id}:{step.action_id or step.type.value}",
                action_name=step.metadata.name or step_id,
                category=ActionCategory.ATOMIC,  # 默认类别
                status=ExecutionStatus.RUNNING,
                params=step.params,
                depth=len(ctx.execution_stack),
                mid=int(mid),
                parent_execution_id=execution_id,
            )

            # 执行
            result = await self._execute_single_step(
                step=step,
                step_group=step_group,
                ctx=ctx,
                registry=registry,
                mid=mid,
                execution_id=execution_id,
                step_results=step_results,
            )

            results.append(result)
            step_results[step_id] = result

            # 更新执行记录
            await self._log_execution_complete(
                execution_id=execution_id,
                action_id=f"{step_id}:{step.action_id or step.type.value}",
                status=ExecutionStatus.SUCCESS if result.success else ExecutionStatus.FAILED,
                result_data=result.data,
                error_message=result.error,
                execution_time=result.execution_time,
            )

            # 错误处理
            if not result.success and not step.metadata.continue_on_error:
                break

        return results

    async def _execute_single_step(
        self,
        step: Step,
        step_group: StepGroup,
        ctx: ActionContext,
        registry,
        mid: str,
        execution_id: str,
        step_results: Dict[str, ActionResult],
    ) -> ActionResult:
        """执行单个步骤"""
        try:
            if step.type == StepType.ATOMIC:
                return await self._execute_atomic_step(step, ctx, registry, mid, execution_id)
            elif step.type == StepType.COMPOSITE_REF:
                return await self._execute_composite_ref_step(step, ctx, registry, mid, execution_id)
            elif step.type == StepType.PLUGIN_REF:
                return await self._execute_plugin_ref_step(step, ctx, registry, mid, execution_id)
            elif step.type == StepType.LOOP:
                return await self._execute_loop_step(step, step_group, ctx, registry, mid, execution_id)
            elif step.type == StepType.CONDITIONAL:
                return await self._execute_conditional_step(step, step_group, ctx, registry, mid, execution_id)
            else:
                return ActionResult(
                    success=False,
                    error=f"不支持的步骤类型: {step.type}",
                    action_id=step.id,
                    action_name=step.metadata.name,
                )
        except Exception as e:
            logger.exception(f"执行步骤失败: {step.id}")
            return ActionResult(
                success=False,
                error=str(e),
                action_id=step.id,
                action_name=step.metadata.name,
            )

    async def _execute_atomic_step(
        self,
        step: Step,
        ctx: ActionContext,
        registry,
        mid: str,
        execution_id: str,
    ) -> ActionResult:
        """执行原子动作"""
        if not step.action_id:
            return ActionResult(
                success=False,
                error="原子动作缺少 action_id",
                action_id=step.id,
                action_name=step.metadata.name,
            )

        action = registry.create_action(step.action_id)
        if not action:
            action = await registry.create_action_for_user(step.action_id, mid)

        if not action:
            return ActionResult(
                success=False,
                error=f"未找到动作: {step.action_id}",
                action_id=step.action_id,
                action_name=step.metadata.name,
            )

        ctx.params = self._replace_templates(step.params, ctx)
        return await self._execute_with_plugins(action, ctx, mid, execution_id)

    async def _execute_composite_ref_step(
        self,
        step: Step,
        ctx: ActionContext,
        registry,
        mid: str,
        execution_id: str,
    ) -> ActionResult:
        """执行组合动作引用"""
        if not step.action_id:
            return ActionResult(
                success=False,
                error="组合动作引用缺少 action_id",
                action_id=step.id,
                action_name=step.metadata.name,
            )

        # 从数据库加载组合动作
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ExecutionRecord).where(ExecutionRecord.action_id == step.action_id)
            )
            record = result.first()

        if not record or not record.is_composite():
            return ActionResult(
                success=False,
                error=f"不是有效的组合动作: {step.action_id}",
                action_id=step.id,
                action_name=step.metadata.name,
            )

        # 检查是否有新格式的步骤组
        if record.has_step_group():
            step_group = record.get_step_group()
            if step_group:
                results = await self.execute_step_group(
                    step_group=step_group,
                    ctx=ctx,
                    registry=registry,
                    mid=mid,
                    execution_id=execution_id,
                )
                # 返回最后一个结果
                return results[-1] if results else ActionResult(
                    success=True,
                    data={},
                    action_id=step.id,
                    action_name=step.metadata.name,
                )

        # 使用旧格式
        return await self._execute_with_plugins_action(record, ctx, registry, mid, execution_id)

    async def _execute_plugin_ref_step(
        self,
        step: Step,
        ctx: ActionContext,
        registry,
        mid: str,
        execution_id: str,
    ) -> ActionResult:
        """执行插件引用"""
        # 类似执行组合动作引用
        return await self._execute_composite_ref_step(step, ctx, registry, mid, execution_id)

    async def _execute_loop_step(
        self,
        step: Step,
        step_group: StepGroup,
        ctx: ActionContext,
        registry,
        mid: str,
        execution_id: str,
    ) -> ActionResult:
        """执行循环步骤"""
        if not step.loop_config:
            return ActionResult(
                success=False,
                error="循环步骤缺少配置",
                action_id=step.id,
                action_name=step.metadata.name,
            )

        loop_config = step.loop_config
        iteration_count = 0
        results: List[ActionResult] = []

        # 确定循环次数
        max_iterations = loop_config.max_iterations
        if loop_config.type == "count" and isinstance(loop_config.value, int):
            max_iterations = min(loop_config.value, max_iterations)

        start_time = time.time()

        while iteration_count < max_iterations:
            ctx.variables["loop.index"] = iteration_count
            ctx.variables["loop.index0"] = iteration_count - 1 if iteration_count > 0 else 0

            # 检查循环条件
            if loop_config.type == "while":
                try:
                    if not eval(str(loop_config.value), {}, {"state": ctx.get_all_variables()}):
                        break
                except Exception as e:
                    logger.warning(f"循环条件评估失败: {e}")
                    break

            # 执行子步骤
            if step.children:
                for child_id in step.children:
                    child_step = step_group.get_step(child_id)
                    if child_step:
                        result = await self._execute_single_step(
                            step=child_step,
                            step_group=step_group,
                            ctx=ctx,
                            registry=registry,
                            mid=mid,
                            execution_id=execution_id,
                            step_results={},
                        )
                        results.append(result)

                        if not result.success:
                            break

            iteration_count += 1

            # 检查 until 条件
            if loop_config.type == "until":
                try:
                    if eval(str(loop_config.value), {}, {"state": ctx.get_all_variables()}):
                        break
                except Exception as e:
                    logger.warning(f"循环退出条件评估失败: {e}")

        total_time = time.time() - start_time
        return ActionResult(
            success=True,
            data={"iterations": iteration_count, "results": [r.data for r in results]},
            execution_time=total_time,
            action_id=step.id,
            action_name=step.metadata.name,
        )

    async def _execute_conditional_step(
        self,
        step: Step,
        step_group: StepGroup,
        ctx: ActionContext,
        registry,
        mid: str,
        execution_id: str,
    ) -> ActionResult:
        """执行条件步骤"""
        if not step.conditional_config:
            return ActionResult(
                success=False,
                error="条件步骤缺少配置",
                action_id=step.id,
                action_name=step.metadata.name,
            )

        conditional_config = step.conditional_config
        condition_result = False

        try:
            condition_result = eval(conditional_config.condition, {}, {"state": ctx.get_all_variables()})
        except Exception as e:
            logger.warning(f"条件评估失败: {e}")

        ctx.variables["condition.result"] = condition_result

        # 选择分支
        branch_id = conditional_config.true_branch if condition_result else conditional_config.false_branch
        if not branch_id:
            return ActionResult(
                success=True,
                data={"condition": condition_result, "branch": None},
                action_id=step.id,
                action_name=step.metadata.name,
            )

        # 执行分支（这里简化为一个步骤，实际可能需要更多逻辑）
        branch_step = step_group.get_step(branch_id)
        if branch_step:
            return await self._execute_single_step(
                step=branch_step,
                step_group=step_group,
                ctx=ctx,
                registry=registry,
                mid=mid,
                execution_id=execution_id,
                step_results={},
            )

        return ActionResult(
            success=True,
            data={"condition": condition_result, "branch": branch_id},
            action_id=step.id,
            action_name=step.metadata.name,
        )

    async def _build_execution_graph(
        self, steps: List[Dict[str, Any]], ctx: ActionContext
    ) -> ExecutionPlan:
        """
        构建执行图

        DP 算法核心：
        1. 遍历 steps，构建节点列表
        2. 检测循环引用
        3. 生成拓扑排序顺序

        Args:
            steps: 步骤列表
            ctx: 执行上下文

        Returns:
            ExecutionPlan: 执行计划
        """
        execution_id = str(uuid.uuid4())
        nodes: List[ExecutionNode] = []
        visited: Set[str] = set()

        for i, step in enumerate(steps):
            action_id = step.get("action_id")
            if not action_id:
                continue

            # 检测循环引用
            if action_id in visited and action_id not in ["loop", "if_else"]:
                logger.warning(f"检测到循环引用: {action_id}")
                continue

            visited.add(action_id)

            # 替换参数中的模板变量
            params = self._replace_templates(step.get("params", {}), ctx)

            node = ExecutionNode(
                action_id=action_id,
                params=params,
                depth=i,
            )
            nodes.append(node)

        # 拓扑排序（简单的顺序执行）
        execution_order = list(range(len(nodes)))

        # 构建依赖图
        dependency_graph = defaultdict(list)
        for i, node in enumerate(nodes):
            dependency_graph[i] = []

        return ExecutionPlan(
            execution_id=execution_id,
            nodes=nodes,
            execution_order=execution_order,
            dependency_graph=dependency_graph,
        )

    def _replace_templates(
        self, params: Dict[str, Any], ctx: ActionContext
    ) -> Dict[str, Any]:
        """
        替换模板变量

        支持格式：
        - {{variable}} - 从变量中获取
        - {{state.key}} - 从状态中获取
        - {{result.index}} - 从历史结果中获取

        Args:
            params: 参数字典
            ctx: 执行上下文

        Returns:
            替换后的参数字典
        """
        def replace_value(value: Any) -> Any:
            if isinstance(value, str):
                def replacer(match):
                    template = match.group(1)
                    parts = template.split(".")

                    # 1. 尝试从 variables 中获取
                    current = ctx.get_all_variables()
                    for part in parts:
                        if isinstance(current, dict):
                            current = current.get(part)
                        else:
                            return match.group(0)

                    if current is not None:
                        return str(current)
                    return match.group(0)

                return re.sub(r"\{\{(.+?)\}\}", replacer, value)
            elif isinstance(value, dict):
                return {k: replace_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [replace_value(item) for item in value]
            return value

        return replace_value(params)

    async def _execute_with_plugins(
        self,
        action: BaseAction,
        ctx: ActionContext,
        mid: str,
        execution_id: str,
    ) -> ActionResult:
        """
        执行 action 并处理插件钩子

        执行顺序：
        1. before_action 插件
        2. 主动作
        3. after_action / on_success / on_error 插件

        Args:
            action: action 实例
            ctx: 执行上下文
            mid: 用户 ID
            execution_id: 执行批次 ID

        Returns:
            ActionResult: 执行结果
        """
        # 1. 执行 before_action 插件
        before_results = await self._execute_plugins_by_hook(
            hook_type=HookType.BEFORE_ACTION,
            target_action_id=action.action_id,
            ctx=ctx,
            mid=mid,
            execution_id=execution_id,
        )

        for plugin_result in before_results:
            if not plugin_result.success:
                logger.warning(f"before_action 插件 '{plugin_result.action_name}' 执行失败")

        # 2. 执行主动作
        result = await action.execute(ctx)

        # 3. 执行后置钩子插件
        if result.success:
            await self._execute_plugins_by_hook(
                hook_type=HookType.AFTER_ACTION,
                target_action_id=action.action_id,
                ctx=ctx,
                mid=mid,
                execution_id=execution_id,
                action_result=result,
            )
            await self._execute_plugins_by_hook(
                hook_type=HookType.ON_SUCCESS,
                target_action_id=action.action_id,
                ctx=ctx,
                mid=mid,
                execution_id=execution_id,
                action_result=result,
            )
        else:
            await self._execute_plugins_by_hook(
                hook_type=HookType.AFTER_ACTION,
                target_action_id=action.action_id,
                ctx=ctx,
                mid=mid,
                execution_id=execution_id,
                action_result=result,
            )
            await self._execute_plugins_by_hook(
                hook_type=HookType.ON_ERROR,
                target_action_id=action.action_id,
                ctx=ctx,
                mid=mid,
                execution_id=execution_id,
                action_result=result,
            )

        return result

    async def _execute_plugins_by_hook(
        self,
        hook_type: HookType,
        target_action_id: str,
        ctx: ActionContext,
        mid: str,
        execution_id: str,
        action_result: Optional[ActionResult] = None,
    ) -> List[ActionResult]:
        """
        按钩子类型执行插件

        Args:
            hook_type: 钩子类型
            target_action_id: 目标 action ID
            ctx: 执行上下文
            mid: 用户 ID
            execution_id: 执行批次 ID
            action_result: 主动作执行结果

        Returns:
            List[ActionResult]: 插件执行结果列表
        """
        async with DatabaseSessionManager.async_session() as session:
            # 查询目标 action 的插件
            result = await session.exec(
                select(ExecutionRecord).where(
                    ExecutionRecord.category == ActionCategory.PLUGIN,
                    ExecutionRecord.hook_type == hook_type.value,
                    ExecutionRecord.target_action_id == target_action_id,
                    ExecutionRecord.is_enabled == True,
                )
            )
            plugins = result.all()

        plugin_results = []
        for plugin in plugins:
            if plugin.mid != int(mid) and not plugin.is_public:
                continue

            try:
                # 创建插件 action 实例
                from app.services.execution.actions.base import PluginAction

                plugin_action = PluginAction(
                    action_id=plugin.action_id,
                    name=plugin.name,
                    hook_type=plugin.hook_type,
                    description=plugin.description,
                    steps=plugin.steps,
                )

                # 记录执行日志
                await self._log_execution(
                    execution_id=execution_id,
                    action_id=plugin.action_id,
                    action_name=plugin.name,
                    category=ActionCategory.PLUGIN,
                    status=ExecutionStatus.RUNNING,
                    params=ctx.params,
                    depth=len(ctx.execution_stack),
                    mid=int(mid),
                )

                # 执行插件
                result = await plugin_action.execute(ctx)
                plugin_results.append(result)

                # 更新执行日志
                await self._log_execution_complete(
                    execution_id=execution_id,
                    action_id=plugin.action_id,
                    status=ExecutionStatus.SUCCESS if result.success else ExecutionStatus.FAILED,
                    result_data={"success": result.success, "error": result.error},
                    error_message=result.error if not result.success else None,
                    execution_time=result.execution_time,
                )

            except Exception as e:
                logger.error(f"插件 '{plugin.name}' 执行失败: {e}")
                error_result = ActionResult(
                    success=False,
                    error=str(e),
                    action_id=plugin.action_id,
                    action_name=plugin.name,
                )
                plugin_results.append(error_result)

                await self._log_execution_complete(
                    execution_id=execution_id,
                    action_id=plugin.action_id,
                    status=ExecutionStatus.FAILED,
                    error_message=str(e),
                )

        return plugin_results

    async def _log_execution(
        self,
        execution_id: str,
        action_id: str,
        action_name: str,
        category: ActionCategory,
        status: ExecutionStatus,
        params: Dict[str, Any],
        depth: int,
        mid: int,
        workflow_id: Optional[str] = None,
        parent_execution_id: Optional[str] = None,
    ) -> str:
        """
        记录执行开始

        Args:
            execution_id: 执行批次 ID
            action_id: action ID
            action_name: action 名称
            category: 动作类别
            status: 执行状态
            params: 执行参数
            depth: 执行深度
            mid: 用户 ID
            workflow_id: 工作流 ID
            parent_execution_id: 父执行 ID

        Returns:
            str: 执行记录 ID
        """
        log_id = str(uuid.uuid4())

        async with DatabaseSessionManager.async_session() as session:
            log = ActionExecutionLog(
                execution_id=execution_id,
                action_id=action_id,
                action_name=action_name,
                category=category,
                status=status,
                params=params,
                depth=depth,
                mid=mid,
                workflow_id=workflow_id,
                parent_execution_id=parent_execution_id,
            )
            session.add(log)
            await session.commit()

        return log_id

    async def _log_execution_complete(
        self,
        execution_id: str,
        action_id: str,
        status: ExecutionStatus,
        result_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        execution_time: float = 0.0,
    ):
        """
        更新执行记录

        Args:
            execution_id: 执行批次 ID
            action_id: action ID
            status: 执行状态
            result_data: 结果数据
            error_message: 错误信息
            execution_time: 执行时长
        """
        async with DatabaseSessionManager.async_session() as session:
            from datetime import datetime

            result = await session.exec(
                select(ActionExecutionLog).where(
                    ActionExecutionLog.execution_id == execution_id,
                    ActionExecutionLog.action_id == action_id,
                    ActionExecutionLog.finished_at == None,
                )
            )
            log = result.first()

            if log:
                log.status = status
                log.result_data = result_data
                log.error_message = error_message
                log.execution_time = execution_time
                log.finished_at = datetime.now()
                await session.commit()

    async def execute_composite(
        self,
        steps: List[Dict[str, Any]],
        ctx: ActionContext,
        registry: Optional['ActionRegistry'] = None,
    ) -> List[ActionResult]:
        """
        执行组合动作（DP 算法）

        DP 算法流程：
        1. 构建执行图
        2. 按拓扑顺序执行
        3. 处理循环和条件
        4. 收集结果

        Args:
            steps: 步骤列表
            ctx: 执行上下文
            registry: action 注册表

        Returns:
            List[ActionResult]: 执行结果列表
        """
        from app.services.execution.action_registry import action_registry

        if registry is None:
            registry = action_registry

        execution_id = str(uuid.uuid4())
        results: List[ActionResult] = []

        # 构建执行计划
        plan = await self._build_execution_graph(steps, ctx)

        logger.info(f"[UnifiedEngine] 开始执行组合动作，包含 {len(plan.nodes)} 个步骤")

        # 按顺序执行
        for i, node in enumerate(plan.nodes):
            # 检查循环
            if node.action_id in ctx.execution_stack:
                logger.warning(f"检测到循环引用，跳过: {node.action_id}")
                continue

            # 添加到执行栈
            ctx.execution_stack.append(node.action_id)

            try:
                # 记录执行开始
                await self._log_execution(
                    execution_id=execution_id,
                    action_id=node.action_id,
                    action_name=node.action_id,
                    category=ActionCategory.ATOMIC,
                    status=ExecutionStatus.RUNNING,
                    params=node.params,
                    depth=i,
                    mid=int(ctx.input.get("mid", 0)),
                )

                # 创建 action 实例
                from app.services.execution.unified_registry import unified_action_registry
                action = registry.create_action(node.action_id)
                if not action:
                    # 尝试从数据库加载
                    action = await registry.create_action_for_user(
                        node.action_id,
                        str(ctx.input.get("mid", "")),
                    )

                if not action:
                    result = ActionResult(
                        success=False,
                        error=f"未找到 action: {node.action_id}",
                        action_id=node.action_id,
                        action_name=node.action_id,
                    )
                else:
                    # 更新上下文参数
                    ctx.params = node.params

                    # 特殊处理控制流
                    if node.action_id == "loop":
                        result = await self._execute_loop(action, ctx, registry)
                    elif node.action_id == "if_else":
                        result = await self._execute_if_else(action, ctx, registry)
                    else:
                        # 执行 action（包含插件钩子）
                        result = await self._execute_with_plugins(
                            action=action,
                            ctx=ctx,
                            mid=str(ctx.input.get("mid", "")),
                            execution_id=execution_id,
                        )

                results.append(result)

                # 更新执行记录
                await self._log_execution_complete(
                    execution_id=execution_id,
                    action_id=node.action_id,
                    status=ExecutionStatus.SUCCESS if result.success else ExecutionStatus.FAILED,
                    result_data={"success": result.success, "data": result.data},
                    error_message=result.error if not result.success else None,
                    execution_time=result.execution_time,
                )

                # 更新变量（从 result.output 同步到 ctx）
                if result.success:
                    if result.output:
                        for name, value in result.output.items():
                            ctx.set_output(name, value)
                    if result.data and isinstance(result.data, dict):
                        for key, value in result.data.items():
                            ctx.set_variable(key, value)
                    ctx.set_variable(f"result_{i}", result.data)

                # 失败时停止
                if not result.success and ctx.input.get("on_error") == "stop":
                    break

            except Exception as e:
                logger.error(f"执行 action '{node.action_id}' 失败: {e}")
                result = ActionResult(
                    success=False,
                    error=str(e),
                    action_id=node.action_id,
                    action_name=node.action_id,
                )
                results.append(result)

                await self._log_execution_complete(
                    execution_id=execution_id,
                    action_id=node.action_id,
                    status=ExecutionStatus.FAILED,
                    error_message=str(e),
                )

            finally:
                # 从执行栈移除
                if ctx.execution_stack and ctx.execution_stack[-1] == node.action_id:
                    ctx.execution_stack.pop()

        return results

    async def _execute_loop(
        self,
        action: BaseAction,
        ctx: ActionContext,
        registry: 'ActionRegistry',
    ) -> ActionResult:
        """
        执行循环动作

        Args:
            action: loop action 实例
            ctx: 执行上下文
            registry: action 注册表

        Returns:
            ActionResult: 执行结果
        """
        loop_count = ctx.params.get("loop_count")
        loop_while = ctx.params.get("loop_while")
        loop_until = ctx.params.get("loop_until")
        children = ctx.params.get("children", [])

        if not children:
            return ActionResult(
                success=False,
                error="循环体为空",
                action_id="loop",
            )

        results = []
        iteration = 0
        max_iterations = loop_count or 100

        while True:
            iteration += 1
            ctx.set_variable("loop_index", iteration)

            # 检查循环条件
            if loop_count and iteration > loop_count:
                break

            if loop_while:
                try:
                    if not eval(loop_while, {"__builtins__": {}}, {"state": ctx.get_all_variables()}):
                        break
                except Exception as e:
                    logger.warning(f"loop_while 评估失败: {e}")

            if loop_until:
                try:
                    if eval(loop_until, {"__builtins__": {}}, {"state": ctx.get_all_variables()}):
                        break
                except Exception as e:
                    logger.warning(f"loop_until 评估失败: {e}")

            if iteration > max_iterations:
                logger.warning(f"循环次数超过限制: {max_iterations}")
                break

            # 执行循环体
            loop_results = await self.execute_composite(children, ctx, registry)
            results.extend(loop_results)

            # 如果循环体失败，停止
            if not loop_results[-1].success if loop_results else True:
                break

        return ActionResult(
            success=True,
            data={
                "iterations": iteration,
                "results": [
                    {"action_id": r.action_id, "success": r.success}
                    for r in results
                ]
            },
            execution_time=sum(r.execution_time for r in results),
            action_id="loop",
        )

    async def _execute_if_else(
        self,
        action: BaseAction,
        ctx: ActionContext,
        registry: 'ActionRegistry',
    ) -> ActionResult:
        """
        执行条件分支动作

        Args:
            action: if_else action 实例
            ctx: 执行上下文
            registry: action 注册表

        Returns:
            ActionResult: 执行结果
        """
        condition = ctx.params.get("condition")
        true_branch = ctx.params.get("true_branch", [])
        false_branch = ctx.params.get("false_branch", [])

        # 评估条件
        condition_result = False
        if condition:
            try:
                condition_result = eval(condition, {"__builtins__": {}}, {"state": ctx.get_all_variables()})
            except Exception as e:
                logger.warning(f"条件评估失败: {e}")

        ctx.set_variable("condition_result", condition_result)

        # 选择分支
        selected_steps = true_branch if condition_result else false_branch
        branch_name = "true_branch" if condition_result else "false_branch"

        if not selected_steps:
            return ActionResult(
                success=True,
                data={"branch": branch_name, "executed": False},
                action_id="if_else",
            )

        # 执行分支
        results = await self.execute_composite(selected_steps, ctx, registry)

        return ActionResult(
            success=results[-1].success if results else True,
            data={
                "branch": branch_name,
                "executed": True,
                "results": [
                    {"action_id": r.action_id, "success": r.success}
                    for r in results
                ]
            },
            execution_time=sum(r.execution_time for r in results),
            action_id="if_else",
        )


unified_execution_engine = UnifiedExecutionEngine()
