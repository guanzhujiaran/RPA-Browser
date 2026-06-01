"""
Action Executor - 简化的动作执行器

核心设计：
1. 创建 action 实例时赋值 page/params/input/output
2. execute() 不传参，action 自身持有所有运行时数据
3. ctx 仅用于共享变量池
"""

import re
import time
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger
from sqlmodel import select

from app.services.execution.actions.base import (
    ActionContext,
    ActionResult,
)
from app.models.database.workflow.unified_models import (
    ActionCategory,
    ExecutionStatus,
    ActionExecutionLog,
)
from app.utils.depends.session_manager import DatabaseSessionManager

if TYPE_CHECKING:
    from app.services.execution.action_registry import ActionRegistry


class ActionExecutor:
    """
    动作执行器
    
    职责：
    1. 根据步骤创建 action 实例（赋值 page/params）
    2. 执行 action.execute(ctx)
    3. 处理循环和条件控制流
    4. 记录执行日志
    """
    
    def __init__(self):
        self._max_depth = 50
    
    async def execute_steps(
        self,
        steps: List[Dict[str, Any]],
        page: Any = None,
        browser: Any = None,
        variables: Optional[Dict[str, Any]] = None,
        registry: Optional['ActionRegistry'] = None,
        mid: str = "0",
        execution_id: Optional[str] = None,
    ) -> List[ActionResult]:
        """
        执行步骤列表
        
        Args:
            steps: 步骤列表
            page: Playwright Page 对象
            browser: Playwright BrowserContext
            variables: 共享变量池
            registry: 动作注册表
            mid: 用户ID
            execution_id: 执行批次ID
            
        Returns:
            执行结果列表
        """
        from app.services.execution.action_registry import action_registry
        
        if registry is None:
            registry = action_registry
        
        execution_id = execution_id or str(uuid.uuid4())
        
        # 构建共享上下文
        ctx = ActionContext(
            page=page,
            browser=browser,
            variables=dict(variables) if variables else {},
        )
        
        results: List[ActionResult] = []
        
        for i, step in enumerate(steps):
            action_id = step.get("action_id")
            if not action_id:
                continue
            
            # 检查执行栈（循环检测）
            if action_id in ctx.execution_stack:
                logger.warning(f"检测到循环引用: {action_id}")
                continue
            
            ctx.execution_stack.append(action_id)
            
            try:
                # 替换模板变量
                params = self._replace_templates(step.get("params", {}), ctx)
                
                # 记录执行
                await self._log_execution(
                    execution_id=execution_id,
                    action_id=action_id,
                    action_name=action_id,
                    status=ExecutionStatus.RUNNING,
                    params=params,
                    depth=i,
                    mid=int(mid),
                )
                
                # 创建并执行动作
                result = await self._execute_single_step(
                    action_id=action_id,
                    params=params,
                    ctx=ctx,
                    page=page,
                    browser=browser,
                    registry=registry,
                    mid=mid,
                    execution_id=execution_id,
                    step=step,
                )
                
                results.append(result)
                
                # 更新执行记录
                await self._log_execution_complete(
                    execution_id=execution_id,
                    action_id=action_id,
                    status=ExecutionStatus.SUCCESS if result.success else ExecutionStatus.FAILED,
                    result_data={"success": result.success, "data": result.data},
                    error_message=result.error,
                    execution_time=result.execution_time,
                )
                
                # 同步 output 到共享变量池
                if result.success:
                    if result.output:
                        for name, value in result.output.items():
                            ctx.set_output(name, value)
                    if result.data and isinstance(result.data, dict):
                        for key, value in result.data.items():
                            ctx.set_var(key, value)
                    ctx.set_var(f"result_{i}", result.data)
                
                # 失败时停止
                if not result.success and params.get("on_error") == "stop":
                    break
                    
            except Exception as e:
                logger.exception(f"执行动作失败: {action_id}")
                error_result = ActionResult(
                    success=False,
                    error=str(e),
                    action_id=action_id,
                    action_name=action_id,
                )
                results.append(error_result)
                
                await self._log_execution_complete(
                    execution_id=execution_id,
                    action_id=action_id,
                    status=ExecutionStatus.FAILED,
                    error_message=str(e),
                )
            
            finally:
                if ctx.execution_stack and ctx.execution_stack[-1] == action_id:
                    ctx.execution_stack.pop()
        
        return results
    
    async def _execute_single_step(
        self,
        action_id: str,
        params: Dict[str, Any],
        ctx: ActionContext,
        page: Any,
        browser: Any,
        registry,
        mid: str,
        execution_id: str,
        step: Dict[str, Any],
    ) -> ActionResult:
        """执行单个步骤"""
        # 特殊处理控制流
        if action_id == "loop":
            return await self._execute_loop(params, ctx, page, browser, registry, mid, execution_id)
        elif action_id == "if_else":
            return await self._execute_if_else(params, ctx, page, browser, registry, mid, execution_id)
        
        # 获取动作类
        action_class = registry.get_action_class(action_id)
        if not action_class:
            # 尝试从数据库加载
            action = await registry.create_action_for_user(action_id, mid)
            if not action:
                return ActionResult(
                    success=False,
                    error=f"未找到动作: {action_id}",
                    action_id=action_id,
                    action_name=action_id,
                )
            # 数据库加载的 action 已有 steps，赋值运行时属性
            action.page = page
            action.browser = browser
            action._variables = dict(ctx.variables)
            return await action.execute(ctx)
        
        # 创建 action 实例，初始化时赋值所有属性
        action = action_class(
            page=page,
            browser=browser,
            params=params,
            input=dict(ctx.variables),
        )
        
        # 执行（ctx 仅用于共享变量池）
        return await action.execute(ctx)
    
    async def _execute_loop(
        self,
        params: Dict[str, Any],
        ctx: ActionContext,
        page: Any,
        browser: Any,
        registry,
        mid: str,
        execution_id: str,
    ) -> ActionResult:
        """执行循环"""
        loop_count = params.get("loop_count")
        loop_while = params.get("loop_while")
        loop_until = params.get("loop_until")
        children = params.get("children", [])
        
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
            ctx.set_var("loop_index", iteration)
            
            if loop_count and iteration > loop_count:
                break
            
            if loop_while:
                try:
                    if not eval(loop_while, {}, {"state": ctx.variables}):
                        break
                except Exception as e:
                    logger.warning(f"loop_while 评估失败: {e}")
            
            if loop_until:
                try:
                    if eval(loop_until, {}, {"state": ctx.variables}):
                        break
                except Exception as e:
                    logger.warning(f"loop_until 评估失败: {e}")
            
            if iteration > max_iterations:
                logger.warning(f"循环次数超过限制: {max_iterations}")
                break
            
            loop_results = await self.execute_steps(
                children, page, browser, ctx.variables, registry, mid, execution_id,
            )
            results.extend(loop_results)
            
            if not loop_results[-1].success if loop_results else True:
                break
        
        return ActionResult(
            success=True,
            data={"iterations": iteration, "results": [{"action_id": r.action_id, "success": r.success} for r in results]},
            execution_time=sum(r.execution_time for r in results),
            action_id="loop",
        )
    
    async def _execute_if_else(
        self,
        params: Dict[str, Any],
        ctx: ActionContext,
        page: Any,
        browser: Any,
        registry,
        mid: str,
        execution_id: str,
    ) -> ActionResult:
        """执行条件分支"""
        condition = params.get("condition")
        true_branch = params.get("true_branch", [])
        false_branch = params.get("false_branch", [])
        
        condition_result = False
        if condition:
            try:
                condition_result = eval(condition, {}, {"state": ctx.variables})
            except Exception as e:
                logger.warning(f"条件评估失败: {e}")
        
        ctx.set_var("condition_result", condition_result)
        
        selected_steps = true_branch if condition_result else false_branch
        branch_name = "true_branch" if condition_result else "false_branch"
        
        if not selected_steps:
            return ActionResult(
                success=True,
                data={"branch": branch_name, "executed": False},
                action_id="if_else",
            )
        
        results = await self.execute_steps(
            selected_steps, page, browser, ctx.variables, registry, mid, execution_id,
        )
        
        last_result = results[-1] if results else None
        return ActionResult(
            success=last_result.success if last_result else True,
            data={"branch": branch_name, "executed": True, "results": [{"action_id": r.action_id, "success": r.success} for r in results]},
            execution_time=sum(r.execution_time for r in results),
            action_id="if_else",
        )
    
    def _replace_templates(
        self,
        params: Dict[str, Any],
        ctx: ActionContext,
    ) -> Dict[str, Any]:
        """替换模板变量"""
        def replace_value(value: Any) -> Any:
            if isinstance(value, str):
                def replacer(match):
                    template = match.group(1)
                    parts = template.split(".")
                    current = ctx.variables
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
    
    async def _log_execution(
        self,
        execution_id: str,
        action_id: str,
        action_name: str,
        status: ExecutionStatus,
        params: Dict[str, Any],
        depth: int,
        mid: int,
        workflow_id: Optional[str] = None,
    ) -> str:
        """记录执行开始"""
        async with DatabaseSessionManager.async_session() as session:
            log = ActionExecutionLog(
                execution_id=execution_id,
                action_id=action_id,
                action_name=action_name,
                category=ActionCategory.ATOMIC,
                status=status,
                params=params,
                depth=depth,
                mid=mid,
                workflow_id=workflow_id,
            )
            session.add(log)
            await session.commit()
        return execution_id
    
    async def _log_execution_complete(
        self,
        execution_id: str,
        action_id: str,
        status: ExecutionStatus,
        result_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        execution_time: float = 0.0,
    ):
        """更新执行记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ActionExecutionLog).where(
                    ActionExecutionLog.execution_id == execution_id,
                    ActionExecutionLog.action_id == action_id,
                    ActionExecutionLog.finished_at == None,
                )
            )
            log = result.first()
            if log:
                from datetime import datetime
                log.status = status
                log.result_data = result_data
                log.error_message = error_message
                log.execution_time = execution_time
                log.finished_at = datetime.now()
                await session.commit()


# 全局实例
action_executor = ActionExecutor()
