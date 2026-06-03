"""
控制流类 Action - Loop, IfElse, CompositeAction
"""
import time
import re
import uuid
from datetime import datetime
from typing import Dict, List
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Any
from dataclasses import field
from botright.playwright_mock import Page
from loguru import logger

from app.services.execution.actions.base import BaseAction, ActionResult
from app.services.execution.actions.all_actions import get_action_class
from app.config import settings
from app.models.database.workflow.models import (
    BuiltinActionType, ExecutionStatus,
    ActionExecutionLog, ActionCategory
)
from app.utils.depends.session_manager import DatabaseSessionManager


class LoopAction(BaseAction):
    """循环控制流操作"""
    action_id: BuiltinActionType = BuiltinActionType.LOOP

    async def execute(self) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.action_id, action_name=self.action_name,
            )

        items = validated_params.items
        count = validated_params.count

        execute_steps_func = self.variables.get("_execute_steps_func")
        if not execute_steps_func:
            return ActionResult(success=False, error="LoopAction 必须在 Workflow 上下文中执行")

        children = self.variables.get("_children_steps", [])
        if not children:
            return ActionResult(success=True, data={"message": "无子步骤可执行"})

        current_depth = self.variables.get("_recursion_depth", 0)
        max_depth = settings.workflow_max_nesting_depth

        if current_depth >= max_depth:
            return ActionResult(
                success=False,
                error=f"嵌套深度超过限制 ({current_depth}/{max_depth})，请简化工作流结构",
                execution_time=time.time() - start_time,
                action_id=self.action_id,
                action_name=self.action_name,
            )

        results = []
        iteration_list = items if items is not None else range(count)

        for index, item in enumerate(iteration_list):
            loop_ctx = {
                "index": index,
                "current_item": item,
                "total": len(iteration_list) if hasattr(iteration_list, '__len__') else count
            }

            self.variables["state"] = self.variables.get("state", {})
            self.variables["state"]["loop"] = loop_ctx

            self.variables["_loop_parent_depth"] = current_depth

            try:
                step_results = await execute_steps_func(children, self)
                iteration_success = all(
                    r.success for r in step_results) if step_results else True
                results.append(
                    {"iteration": index, "success": iteration_success, "results": step_results})
            except Exception as e:
                results.append(
                    {"iteration": index, "success": False, "error": str(e)})
            finally:
                if "_loop_parent_depth" in self.variables:
                    del self.variables["_loop_parent_depth"]

        return ActionResult(
            success=True,
            data={"iterations": len(results), "details": results},
            execution_time=time.time() - start_time,
            action_id=self.action_id, action_name=self.action_name,
        )


class IfElseAction(BaseAction):
    """条件分支控制流操作"""
    action_id: BuiltinActionType = BuiltinActionType.IF_ELSE


    async def execute(self) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.action_id, action_name=self.action_name,
            )

        condition = validated_params.condition

        execute_steps_func = self.variables.get("_execute_steps_func")
        if not execute_steps_func:
            return ActionResult(success=False, error="IfElseAction 必须在 Workflow 上下文中执行")

        current_depth = self.variables.get("_recursion_depth", 0)
        max_depth = settings.workflow_max_nesting_depth

        if current_depth >= max_depth:
            return ActionResult(
                success=False,
                error=f"嵌套深度超过限制 ({current_depth}/{max_depth})，请简化工作流结构",
                execution_time=time.time() - start_time,
                action_id=self.action_id,
                action_name=self.action_name,
            )

        state = self.variables.get("state", {})
        try:
            is_true = eval(condition, {"__builtins__": {}}, {"state": state})
        except:
            is_true = False

        branch_key = "true_branch" if is_true else "false_branch"
        children = self.variables.get(f"_{branch_key}_steps", [])

        if not children:
            return ActionResult(success=True, data={"branch_taken": branch_key, "message": "分支无步骤"})

        self.variables["_ifelse_parent_depth"] = current_depth

        try:
            results = await execute_steps_func(children, self)
            return ActionResult(
                success=True,
                data={"branch_taken": branch_key, "results": results},
                execution_time=time.time() - start_time,
                action_id=self.action_id, action_name=self.action_name,
            )
        except Exception as e:
            return ActionResult(
                success=False, error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.action_id, action_name=self.action_name,
            )
        finally:
            if "_ifelse_parent_depth" in self.variables:
                del self.variables["_ifelse_parent_depth"]


class CompositeAction(BaseAction):
    """组合动作基类"""
    action_id: BuiltinActionType = BuiltinActionType.COMPOSITE
    _max_depth: int = 50 # 系统设置，无需传参

    async def execute(self) -> ActionResult:
        
        results = await self._execute_steps(
            steps=self.steps,
            page=self.page,
            mid=self.mid,
            variables=self.variables,
        )

        total = len(results)
        success_count = sum(1 for r in results if r.success)

        last_result = results[-1] if results else None
        return ActionResult(
            success=last_result.success if last_result else False,
            data={
                "total_steps": total,
                "success_count": success_count,
                "results": [{"action_id": r.action_id, "success": r.success} for r in results]
            },
            error=last_result.error if last_result and not last_result.success else None,
            execution_time=sum(r.execution_time for r in results),
            action_id=self.action_id,
            action_name=self.action_name,
            logs=self.get_logs(),
        )

    async def _execute_steps(
        self,
        *,
        steps: List[Dict[str, Any]],
        page: Page,
        mid: int,
        variables: Dict[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> List[ActionResult]:
        """
        执行步骤列表
        
        Args:
            steps: 步骤列表
            page: Playwright Page 对象
            mid: 用户ID
            variables: 共享变量池
            execution_id: 执行批次ID
            
        Returns:
            执行结果列表
        """
        
        execution_id = execution_id or str(uuid.uuid4())
        
        # 构建共享变量池
        shared_variables = dict(variables) if variables else {}
        execution_stack: List[str] = []
        
        results: List[ActionResult] = []
        
        for i, step in enumerate(steps):
            action_id = step.get("action_id")
            
            # 检查执行栈（循环检测）
            if action_id in execution_stack:
                logger.warning(f"检测到循环引用: {action_id}")
                continue
            
            execution_stack.append(action_id)
            
            try:
                # 替换模板变量
                params = self._replace_templates(step.get("params", {}), shared_variables)
                
                # 记录执行
                await self._log_execution(
                    execution_id=execution_id,
                    action_id=action_id,
                    action_name=action_id,
                    status=ExecutionStatus.RUNNING,
                    params=params,
                    depth=i,
                    mid=mid,
                )
                
                # 创建并执行动作
                result = await self._execute_single_step(
                    action_id=action_id,
                    params=params,
                    page=page,
                    variables=shared_variables,
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
                            shared_variables[name] = value
                    if result.data and isinstance(result.data, dict):
                        for key, value in result.data.items():
                            shared_variables[key] = value
                    shared_variables[f"result_{i}"] = result.data
                
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
                if execution_stack and execution_stack[-1] == action_id:
                    execution_stack.pop()
        
        return results
    
    async def _execute_single_step(
        self,
        action_id: str,
        params: Dict[str, Any],
        page: Any,
        variables: Dict[str, Any],
        mid: int,
        execution_id: str,
        step: Dict[str, Any],
    ) -> ActionResult:
        """执行单个步骤"""
        # 特殊处理控制流
        if action_id == BuiltinActionType.LOOP:
            return await self._execute_loop(params, page, variables, mid, execution_id)
        elif action_id == BuiltinActionType.IF_ELSE:
            return await self._execute_if_else(params, page, variables, mid, execution_id)
        
        # 获取动作类（使用全局注册表）
        action_class = get_action_class(action_id)
        
        # 创建 action 实例，初始化时赋值所有属性
        action = action_class(
            page=page,
            params=params,
            variables=dict(variables),
        )
        
        # 执行
        return await action.execute()
    
    async def _execute_loop(
        self,
        params: Dict[str, Any],
        page: Any,
        variables: Dict[str, Any],
        mid: int,
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
            variables["loop_index"] = iteration
            
            if loop_count and iteration > loop_count:
                break
            
            if loop_while:
                try:
                    if not eval(loop_while, {}, {"state": variables}):
                        break
                except Exception as e:
                    logger.warning(f"loop_while 评估失败: {e}")
            
            if loop_until:
                try:
                    if eval(loop_until, {}, {"state": variables}):
                        break
                except Exception as e:
                    logger.warning(f"loop_until 评估失败: {e}")
            
            if iteration > max_iterations:
                logger.warning(f"循环次数超过限制: {max_iterations}")
                break
            
            loop_results = await self._execute_steps(
                steps=children,
                page=page,
                mid=mid,
                variables=variables,
                execution_id=execution_id,
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
        page: Any,
        variables: Dict[str, Any],
        mid: int,
        execution_id: str,
    ) -> ActionResult:
        """执行条件分支"""
        condition = params.get("condition")
        true_branch = params.get("true_branch", [])
        false_branch = params.get("false_branch", [])
        
        condition_result = False
        if condition:
            try:
                condition_result = eval(condition, {}, {"state": variables})
            except Exception as e:
                logger.warning(f"条件评估失败: {e}")
        
        variables["condition_result"] = condition_result
        
        selected_steps = true_branch if condition_result else false_branch
        branch_name = "true_branch" if condition_result else "false_branch"
        
        if not selected_steps:
            return ActionResult(
                success=True,
                data={"branch": branch_name, "executed": False},
                action_id=BuiltinActionType.IF_ELSE,
            )
        
        results = await self._execute_steps(
            steps=selected_steps,
            page=page,
            mid=mid,
            variables=variables,
            execution_id=execution_id,
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
        variables: Dict[str, Any],
    ) -> Dict[str, Any]:
        """替换模板变量"""
        def replace_value(value: Any) -> Any:
            if isinstance(value, str):
                def replacer(match):
                    template = match.group(1)
                    parts = template.split(".")
                    current = variables
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
        workflow_id: str | None = None,
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
        result_data: Dict[str, Any] | None = None,
        error_message: str | None = None,
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
                log.status = status
                log.result_data = result_data
                log.error_message = error_message
                log.execution_time = execution_time
                log.finished_at = datetime.now()
                await session.commit()
