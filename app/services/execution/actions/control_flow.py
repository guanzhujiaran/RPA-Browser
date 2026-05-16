"""
控制流类 Action - Loop, IfElse, Composite
"""
import time
from typing import Any

from app.services.execution.actions.base import BaseAction
from app.models.execution.params import LoopParams, IfElseParams
from app.models.core.workflow.models import ActionType, ActionMetadata, ActionResult, ActionContext, CustomActionModel
from sqlmodel import select
from app.utils.depends.session_manager import DatabaseSessionManager
from app.config import settings


class LoopAction(BaseAction):
    """循环控制流操作"""

    params_model = LoopParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="loop", name="循环", type=ActionType.CUSTOM,
            description="遍历列表或重复执行子步骤",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()
        
        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        items = validated_params.items
        count = validated_params.count
        
        execute_steps_func = ctx.user_data.get("_execute_steps_func")
        if not execute_steps_func:
            return ActionResult(success=False, error="LoopAction 必须在 Workflow 上下文中执行")

        children = ctx.user_data.get("_children_steps", [])
        if not children:
            return ActionResult(success=True, data={"message": "无子步骤可执行"})

        # 检查嵌套深度（与执行引擎的递归深度共享）
        current_depth = ctx.user_data.get("_recursion_depth", 0)
        max_depth = settings.workflow_max_nesting_depth
        
        if current_depth >= max_depth:
            return ActionResult(
                success=False,
                error=f"嵌套深度超过限制 ({current_depth}/{max_depth})，请简化工作流结构",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        results = []
        iteration_list = items if items is not None else range(count)
        
        for index, item in enumerate(iteration_list):
            loop_ctx = {
                "index": index,
                "current_item": item,
                "total": len(iteration_list) if hasattr(iteration_list, '__len__') else count
            }
            
            ctx.user_data["state"] = ctx.user_data.get("state", {})
            ctx.user_data["state"]["loop"] = loop_ctx
            
            # 注意：不需要手动增加深度，因为 execute_steps_func 会在 _execute_steps 中自动增加
            # 这里只是保存当前深度用于恢复
            ctx.user_data["_loop_parent_depth"] = current_depth

            try:
                step_results = await execute_steps_func(children, ctx)
                results.append({"iteration": index, "success": True, "results": step_results})
            except Exception as e:
                results.append({"iteration": index, "success": False, "error": str(e)})
                break
            finally:
                # 恢复父级深度（实际上 _execute_steps 会自动管理）
                if "_loop_parent_depth" in ctx.user_data:
                    del ctx.user_data["_loop_parent_depth"]

        return ActionResult(
            success=True,
            data={"iterations": len(results), "details": results},
            execution_time=time.time() - start_time,
            action_id=self.metadata.id, action_name=self.metadata.name,
        )


class IfElseAction(BaseAction):
    """条件分支控制流操作"""

    params_model = IfElseParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="if_else", name="条件分支", type=ActionType.CUSTOM,
            description="根据条件执行 true/false 分支",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()
        
        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        condition = validated_params.condition
        
        execute_steps_func = ctx.user_data.get("_execute_steps_func")
        if not execute_steps_func:
            return ActionResult(success=False, error="IfElseAction 必须在 Workflow 上下文中执行")

        # 检查嵌套深度（与执行引擎的递归深度共享）
        current_depth = ctx.user_data.get("_recursion_depth", 0)
        max_depth = settings.workflow_max_nesting_depth
        
        if current_depth >= max_depth:
            return ActionResult(
                success=False,
                error=f"嵌套深度超过限制 ({current_depth}/{max_depth})，请简化工作流结构",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        state = ctx.user_data.get("state", {})
        try:
            is_true = eval(condition, {"__builtins__": {}}, {"state": state})
        except:
            is_true = False

        branch_key = "true_branch" if is_true else "false_branch"
        children = ctx.user_data.get(f"_{branch_key}_steps", [])

        if not children:
            return ActionResult(success=True, data={"branch_taken": branch_key, "message": "分支无步骤"})

        # 注意：不需要手动增加深度，因为 execute_steps_func 会在 _execute_steps 中自动增加
        # 这里只是保存当前深度用于恢复
        ctx.user_data["_ifelse_parent_depth"] = current_depth

        try:
            results = await execute_steps_func(children, ctx)
            return ActionResult(
                success=True,
                data={"branch_taken": branch_key, "results": results},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
        except Exception as e:
            return ActionResult(
                success=False, error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
        finally:
            # 恢复父级深度（实际上 _execute_steps 会自动管理）
            if "_ifelse_parent_depth" in ctx.user_data:
                del ctx.user_data["_ifelse_parent_depth"]


class CompositeAction(BaseAction):
    """组合操作"""

    def __init__(self, action_id: str, name: str, description: str, steps: list[dict[str, Any]]):
        self._action_id = action_id
        self._name = name
        self._description = description
        self._steps = steps
        self._registry = None

    def set_registry(self, registry):
        """设置操作注册表引用"""
        self._registry = registry

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id=self._action_id, name=self._name, type=ActionType.CUSTOM,
            description=self._description, parameters=[], requires_browser=True,
        )

    def _replace_params(self, text: str, params: dict[str, Any]) -> Any:
        """替换模板参数"""
        if isinstance(text, str):
            result = text
            for key, value in params.items():
                result = result.replace(f"{{{{{key}}}}}", str(value))
            return result
        elif isinstance(text, dict):
            return {k: self._replace_params(v, params) for k, v in text.items()}
        elif isinstance(text, list):
            return [self._replace_params(item, params) for item in text]
        return text

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        if not self._registry:
            return ActionResult(
                success=False, error="操作注册表未初始化",
                execution_time=time.time() - start_time,
                action_id=self._action_id, action_name=self._name,
            )

        # 🔒 循环引用检测：检查当前动作是否已经在执行栈中
        execution_stack = ctx.user_data.setdefault("_execution_stack", [])
        if self._action_id in execution_stack:
            cycle_path = " → ".join(execution_stack + [self._action_id])
            error_msg = f"检测到组合动作循环引用: {cycle_path}"
            logger.error(f"🚫 {error_msg}")
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self._action_id,
                action_name=self._name,
                logs=[f"循环引用检测失败，执行栈: {execution_stack}"]
            )
        
        # 将当前动作加入执行栈
        execution_stack.append(self._action_id)

        results = []
        try:
            for i, step in enumerate(self._steps):
                step_params = self._replace_params(step.get("params", {}), ctx.params)

                sub_ctx = ActionContext(
                    session_id=ctx.session_id, browser_id=ctx.browser_id,
                    page=ctx.page, browser=ctx.browser,
                    params=step_params, user_data=ctx.user_data,
                )

                sub_action = self._registry.create_action(step["action_id"])
                if not sub_action:
                    return ActionResult(
                        success=False, error=f"未找到子操作: {step['action_id']}",
                        execution_time=time.time() - start_time,
                        action_id=self._action_id, action_name=self._name,
                    )

                result = await sub_action.execute(sub_ctx)
                results.append(result)

                if not result.success:
                    return ActionResult(
                        success=False, error=f"步骤 {i+1} 失败: {result.error}",
                        data={"results": [r.__dict__ for r in results]},
                        execution_time=time.time() - start_time,
                        action_id=self._action_id, action_name=self._name,
                    )

            return ActionResult(
                success=True,
                data={
                    "composite": True, "steps_count": len(self._steps),
                    "results": [r.__dict__ for r in results],
                },
                execution_time=time.time() - start_time,
                action_id=self._action_id, action_name=self._name,
            )
        finally:
            # 从执行栈中移除当前动作（无论成功与否）
            if execution_stack and execution_stack[-1] == self._action_id:
                execution_stack.pop()
