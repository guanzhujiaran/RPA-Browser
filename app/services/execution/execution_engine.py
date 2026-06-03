"""
Execution Engine - 操作执行引擎

提供操作执行、工作流执行等功能。
"""

from app.services.execution.actions.base import BaseAction
from botright.playwright_mock.page import Page
import re
import ast
import time
import asyncio
from typing import Any, Dict, List

from loguru import logger

from app.models.database.workflow.models import (
    WorkflowStep,
)
from app.services.RPA_browser.live_service import LiveService
from app.services.execution.crud_service import action_crud, plugin_crud
from app.models.database.workflow.models import (
    ActionMetadata,
    ActionResult,
)
from app.models.execution.params import (
    ExecutionRequest,
    ActionExecutionRequest,
    StepExecutionRequest,
    WorkflowExecutionRequest,
)


# ============ 安全评估 ============

def _safe_eval(condition: str, state: Dict[str, Any]) -> bool:
    """
    安全地评估条件表达式
    
    仅允许基础算术运算、比较运算、布尔运算和变量引用
    禁止函数调用、属性访问和危险操作
    """
    try:
        tree = ast.parse(condition, mode="eval")
        
        allowed_nodes = (
            ast.Expression,
            ast.BoolOp,
            ast.And,
            ast.Or,
            ast.Compare,
            ast.Eq,
            ast.NotEq,
            ast.Lt,
            ast.LtE,
            ast.Gt,
            ast.GtE,
            ast.Name,
            ast.Load,
            ast.Constant,
            ast.Str,
            ast.Num,
            ast.UnaryOp,
            ast.Not,
            ast.UAdd,
            ast.USub,
            ast.BinOp,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.Mod,
            ast.List,
            ast.Tuple,
            ast.Subscript,
            ast.Index,
            ast.Slice,
            ast.Attribute,
        )
        
        for node in ast.walk(tree):
            if not isinstance(node, allowed_nodes):
                logger.warning(
                    f"条件表达式包含不允许的节点: {type(node).__name__}")
                return False
        
        return bool(eval(compile(tree, "<expr>", "eval"), {}, {"state": state}))
    except Exception as e:
        logger.error(f"条件表达式评估失败: {e}")
        return False


class ExecutionEngine:
    """
    执行引擎
    主要的action执行逻辑放在了composite_action.py中
    """

    def __init__(self, **kwargs):
        """
        初始化执行引擎

        Args:
            **kwargs: 引擎配置参数，包括：
                - max_depth: 最大嵌套深度
                - timeout: 默认超时时间
        """
        if kwargs.get("max_depth"):
            self.max_depth = kwargs["max_depth"]
        if kwargs.get("timeout"):
            self.default_timeout = kwargs["timeout"]

    async def _execute_plugins(
        self,
        hook_type: str,
        action_id: str,
        session_id: str,
        browser_id: str,
        page: Page,
        variables: Dict[str, Any] | None = None,
        mid: int = 0,
    ) -> List[ActionResult]:
        """
        执行指定钩子类型的插件

        Args:
            hook_type: 钩子类型
            action_id: 触发插件的动作ID
            session_id: 会话ID
            browser_id: 浏览器ID
            page: 页面
            variables: 变量池
            mid: 用户 mid

        Returns:
            插件执行结果列表
        """
        plugin_results = []

        try:
            plugins = await plugin_crud.get_plugins_by_hook(hook_type)

            for plugin_model in plugins:
                plugin_id = plugin_model.plugin_id

                if not plugin_model.custom_action_id:
                    logger.warning(f"[Plugin] 插件 {plugin_id} 没有关联自定义动作")
                    continue

                logger.info(
                    f"[Plugin] 执行插件: {plugin_model.name} (hook={hook_type})")

                try:
                    plugin_start_time = time.time()
                    plugin_req = ActionExecutionRequest(
                        mid=mid,
                        browser_id=int(browser_id) if browser_id.isdigit() else 0,
                        action_id=plugin_model.custom_action_id,
                        params=plugin_model.config_params if hasattr(plugin_model, 'config_params') else {},
                        variables=variables or {},
                    )
                    plugin_result = await self.execute_action(
                        plugin_req,
                        session_id=session_id,
                        browser_id=browser_id,
                        page=page,
                    )
                    plugin_result.execution_time = time.time() - plugin_start_time
                    plugin_results.append(plugin_result)

                    logger.info(
                        f"[Plugin] 插件 '{plugin_model.name}' 执行完成: "
                        f"success={plugin_result.success}, "
                        f"time={plugin_result.execution_time:.2f}s"
                    )
                except Exception as e:
                    logger.error(
                        f"[Plugin] 插件 '{plugin_model.name}' 执行失败: {e}")
                    plugin_results.append(ActionResult(
                        success=False,
                        error=str(e),
                        execution_time=time.time() - plugin_start_time,
                        action_id=plugin_model.custom_action_id,
                        action_name=f"Plugin: {plugin_model.name}",
                    ))

        except Exception as e:
            logger.error(f"[Plugin] 加载插件配置失败: {e}")

        return plugin_results

    async def execute_action(
        self,
        req: ActionExecutionRequest,
        *,
        session_id: str,
        browser_id: str,
        page: Page,
    ) -> ActionResult:
        """
        执行单个操作

        Args:
            req: 操作执行请求参数
            session_id: 会话ID
            browser_id: 浏览器ID
            page: Playwright Page对象（默认页面）

        Returns:
            ActionResult: 操作结果
        """
        start_time = time.time()

        target_page = page
        if req.page_index is not None:
            session_key = LiveService._get_session_key(
                int(session_id.split("_")[
                    0]) if "_" in session_id else int(session_id),
                int(browser_id),
            )

            if session_key in LiveService.browser_sessions:
                entry = LiveService.browser_sessions[session_key]
                all_pages = await entry.plugined_session.get_all_pages()

                if 0 <= req.page_index < len(all_pages):
                    target_page = all_pages[req.page_index]
                    logger.info(f"📄 使用页面索引 {req.page_index}: {target_page.url}")
                else:
                    return ActionResult(
                        success=False,
                        error=f"页面索引 {req.page_index} 超出范围 (0-{len(all_pages)-1})",
                        execution_time=0,
                        action_id=req.action_id,
                    )
            else:
                logger.debug(f"会话 {session_key} 不存在，使用默认页面")

        action_class = await self._action_registry.get_action_class_for_user(req.action_id, req.mid)
        if not action_class:
            return ActionResult(
                success=False,
                error=f"未找到操作: {req.action_id}",
                execution_time=0,
                action_id=req.action_id,
            )
        action: BaseAction = action_class(
            action_id=req.action_id,
            action_name=action_class.action_name,
            page=page,
            params=req.params,
            input=req.input_data,
            output=req.output,
        )

        valid, error_msg = action.validate_params(req.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=0, action_id=req.action_id
            )

        try:
            before_plugins = await self._execute_plugins(
                hook_type="before_action",
                action_id=req.action_id,
                session_id=session_id,
                browser_id=browser_id,
                page=target_page,
                variables=req.variables,
                mid=req.mid,
            )

            for plugin_result in before_plugins:
                if not plugin_result.success:
                    return ActionResult(
                        success=False,
                        error=f"前置插件执行失败: {plugin_result.error}",
                        execution_time=time.time() - start_time,
                        action_id=req.action_id,
                        action_name=action.action_name,
                    )

            logger.info(
                f"🚀 开始执行动作: {action.action_name} ({req.action_id})")

            result = await action.execute()

            after_plugins = await self._execute_plugins(
                hook_type="after_action",
                action_id=req.action_id,
                session_id=session_id,
                browser_id=browser_id,
                page=target_page,
                variables=req.variables,
                mid=req.mid,
            )

            for plugin_result in after_plugins:
                if not plugin_result.success:
                    logger.warning(
                        f"[Plugin] 后置插件执行失败: {plugin_result.error}")

            result.execution_time = time.time() - start_time
            result.action_id = req.action_id
            result.action_name = action.action_name

            success_plugins = after_plugins
            error_plugins = []

            if result.success:
                success_plugins = await self._execute_plugins(
                    hook_type="on_success",
                    action_id=req.action_id,
                    session_id=session_id,
                    browser_id=browser_id,
                    page=target_page,
                    variables=req.variables,
                    mid=req.mid,
                )
            else:
                error_plugins = await self._execute_plugins(
                    hook_type="on_error",
                    action_id=req.action_id,
                    session_id=session_id,
                    browser_id=browser_id,
                    page=target_page,
                    variables=req.variables,
                    mid=req.mid,
                )

            return result

        except asyncio.TimeoutError:
            timeout_plugins = await self._execute_plugins(
                hook_type="on_timeout",
                action_id=req.action_id,
                session_id=session_id,
                browser_id=browser_id,
                page=target_page,
                variables=req.variables,
                mid=req.mid,
            )
            return ActionResult(
                success=False,
                error="操作执行超时",
                execution_time=time.time() - start_time,
                action_id=req.action_id,
                action_name=action.action_name,
            )
        except Exception as e:
            logger.error(f"操作执行失败: {req.action_id} - {e}")
            error_plugins = await self._execute_plugins(
                hook_type="on_error",
                action_id=req.action_id,
                session_id=session_id,
                browser_id=browser_id,
                page=target_page,
                variables=req.variables,
                mid=req.mid,
            )
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=req.action_id,
                action_name=action.action_name,
            )

    async def _execute_steps(
        self,
        req: ExecutionRequest,
        *,
        steps: List[WorkflowStep],
        session_id: str,
        browser_id: str,
        page: Page,
        depth: int = 0,
    ) -> List[ActionResult]:
        """
        内部方法：执行步骤列表

        Args:
            req: 执行请求基础参数
            steps: 步骤列表
            session_id: 会话ID
            browser_id: 浏览器ID
            page: 页面
            depth: 当前嵌套深度

        Returns:
            执行结果列表
        """
        results = []

        for step in steps:
            try:
                if step.condition:
                    condition_result = _safe_eval(step.condition, req.variables)
                    if not condition_result:
                        logger.info(f"跳过步骤 {step.action_id}（条件不满足）")
                        continue

                replaced_params = self._replace_params(step.params, req.variables)

                if step.action_id == "loop" and step.children:
                    if step.loop_count is not None:
                        for i in range(step.loop_count):
                            req.variables["loop_index"] = i
                            loop_results = await self._execute_steps(
                                req, steps=step.children, session_id=session_id, browser_id=browser_id, page=page,
                                depth=depth + 1,
                            )
                            results.extend(loop_results)
                            if not loop_results[-1].success if loop_results else False:
                                break

                    elif step.loop_while:
                        while _safe_eval(step.loop_while, req.variables):
                            loop_results = await self._execute_steps(
                                req, steps=step.children, session_id=session_id, browser_id=browser_id, page=page,
                                depth=depth + 1,
                            )
                            results.extend(loop_results)
                            if not loop_results[-1].success if loop_results else False:
                                break

                    elif step.loop_until:
                        while True:
                            loop_results = await self._execute_steps(
                                req, steps=step.children, session_id=session_id, browser_id=browser_id, page=page,
                                depth=depth + 1,
                            )
                            results.extend(loop_results)
                            if _safe_eval(step.loop_until, req.variables):
                                break
                            if not loop_results[-1].success if loop_results else False:
                                break

                elif step.action_id == "if_else" and step.children:
                    condition = step.params.get("condition", "")
                    condition_result = _safe_eval(condition, req.variables)

                    true_branch = (
                        step.children[0].children if step.children else []
                    )
                    false_branch = (
                        step.children[1].children if len(
                            step.children) > 1 else []
                    )

                    if condition_result:
                        branch_results = await self._execute_steps(
                            req, steps=true_branch, session_id=session_id, browser_id=browser_id, page=page,
                            depth=depth + 1,
                        )
                    else:
                        branch_results = await self._execute_steps(
                            req, steps=false_branch, session_id=session_id, browser_id=browser_id, page=page,
                            depth=depth + 1,
                        )
                    results.extend(branch_results)

                else:
                    req.variables["_execute_steps_func"] = self._execute_steps

                    step_req = ActionExecutionRequest(
                        mid=req.variables.get("mid", req.mid),
                        browser_id=req.browser_id,
                        variables=req.variables,
                        page_index=req.page_index,
                        action_id=step.action_id,
                        params=replaced_params,
                    )

                    result = await self.execute_action(
                        step_req,
                        session_id=session_id,
                        browser_id=browser_id,
                        page=page,
                    )
                    results.append(result)

                    if step.output_var and result.success:
                        req.variables.setdefault("state", {})[
                            step.output_var] = result.data

                    if not result.success:
                        if step.retry > 0:
                            for retry_i in range(step.retry):
                                logger.info(
                                    f"重试步骤 {step.action_id} ({retry_i + 1}/{step.retry})"
                                )
                                retry_req = ActionExecutionRequest(
                                    mid=req.variables.get("mid", req.mid),
                                    browser_id=req.browser_id,
                                    variables=req.variables,
                                    page_index=req.page_index,
                                    action_id=step.action_id,
                                    params=replaced_params,
                                )
                                retry_result = await self.execute_action(
                                    retry_req,
                                    session_id=session_id,
                                    browser_id=browser_id,
                                    page=page,
                                )
                                results[-1] = retry_result
                                if retry_result.success:
                                    break
                        else:
                            break

            except Exception as e:
                logger.error(f"步骤执行异常: {step.action_id} - {e}")
                results.append(ActionResult(
                    success=False,
                    error=str(e),
                    execution_time=0,
                    action_id=step.action_id,
                    action_name=step.action_id,
                ))
                break

        return results

    def _replace_params(
        self,
        params: Dict[str, Any],
        variables: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        替换参数中的模板变量

        支持 {{variable_name}} 格式的变量替换
        """
        params_str = str(params)

        def replacer(match):
            variable_name = match.group(1)
            value = variables.get(variable_name)
            if value is not None:
                return str(value)
            return match.group(0)

        replaced_str = re.sub(r"\{\{(\w+)\}\}", replacer, params_str)

        return eval(replaced_str)

    async def _ensure_page_ready(self, page: Page) -> None:
        """
        确保页面已就绪
        """
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass


    def get_all_action_metadatas(self) -> List[ActionMetadata]:
        return action_registry.get_all_action_metadatas()

    @staticmethod
    async def execute_with_session(
        req: ActionExecutionRequest,
    ) -> ActionResult:
        """
        🔑 Service 层方法：执行操作（自动管理会话和页面）

        Args:
            req: 操作执行请求参数

        Returns:
            ActionResult: 操作结果
        """
        from app.services.execution.execution_engine import ExecutionEngine

        session_key = LiveService._get_session_key(req.mid, req.browser_id)
        entry = LiveService.browser_sessions.get(session_key)
        if not entry:
            raise ValueError("浏览器不存在或未运行")

        execution_engine = ExecutionEngine()

        if req.page_index is not None:
            all_pages = await entry.plugined_session.get_all_pages()

            if not all_pages:
                raise ValueError(f"浏览器 {req.browser_id} 没有打开任何页面")

            if 0 <= req.page_index < len(all_pages):
                page = all_pages[req.page_index]
                logger.info(f"📄 使用页面索引 {req.page_index}: {page.url}")
            else:
                raise ValueError(
                    f"页面索引 {req.page_index} 超出范围 (0-{len(all_pages)-1})"
                )
        else:
            page = await entry.plugined_session.get_current_page()

        return await execution_engine.execute_action(
            req,
            session_id=str(req.browser_id),
            browser_id=str(req.browser_id),
            page=page,
        )

    @staticmethod
    async def execute_action_step_with_session(
        req: StepExecutionRequest,
    ) -> tuple:
        """
        🔑 Service 层方法：单步执行操作（自动管理会话和页面）

        Args:
            req: 步骤执行请求参数

        Returns:
            tuple: (step_index, action_id, action_name, ActionResult)
        """
        session_key = LiveService._get_session_key(req.mid, req.browser_id)
        entry = LiveService.browser_sessions.get(session_key)
        if not entry:
            raise ValueError("浏览器不存在或未运行")

        execution_engine = ExecutionEngine()

        if req.page_index is not None:
            all_pages = await entry.plugined_session.get_all_pages()

            if not all_pages:
                raise ValueError(f"浏览器 {req.browser_id} 没有打开任何页面")

            if 0 <= req.page_index < len(all_pages):
                page = all_pages[req.page_index]
                logger.info(f"📄 使用页面索引 {req.page_index}: {page.url}")
            else:
                raise ValueError(
                    f"页面索引 {req.page_index} 超出范围 (0-{len(all_pages)-1})"
                )
        else:
            page = await entry.plugined_session.get_current_page()

        action_class = await action_registry.get_action_class_for_user(
            req.action_id, req.mid)
        if not action_class:
            raise ValueError(f"未找到操作: {req.action_id}")
        action_instance = action_class()
        metadata = action_registry.get_action_metadata(req.action_id)
        if not metadata:
            raise ValueError(f"未找到操作: {req.action_id}")

        composite = action_instance if hasattr(
            action_instance, "_steps") else None

        if composite and hasattr(composite, "_steps"):
            steps = composite._steps
            if req.step_index < 0 or req.step_index >= len(steps):
                raise ValueError(f"步骤索引 {req.step_index} 超出范围 (0-{len(steps)-1})")

            step = steps[req.step_index]
            step_params = composite._replace_params(
                step.get("params", {}), req.params)

            step_req = ActionExecutionRequest(
                mid=req.mid,
                browser_id=req.browser_id,
                variables=req.variables,
                page_index=req.page_index,
                action_id=step["action_id"],
                params=step_params,
            )

            result = await execution_engine.execute_action(
                step_req,
                session_id=str(req.browser_id),
                browser_id=str(req.browser_id),
                page=page,
            )

            return (req.step_index, step["action_id"], metadata.name, result)
        else:
            step_req = ActionExecutionRequest(
                mid=req.mid,
                browser_id=req.browser_id,
                variables=req.variables,
                page_index=req.page_index,
                action_id=req.action_id,
                params=req.params,
            )

            result = await execution_engine.execute_action(
                step_req,
                session_id=str(req.browser_id),
                browser_id=str(req.browser_id),
                page=page,
            )

            return (0, req.action_id, metadata.name, result)

    @staticmethod
    async def execute_workflow_with_session(
        req: WorkflowExecutionRequest,
    ) -> List[ActionResult]:
        """
        🔑 Service 层方法：执行工作流（自动管理会话和页面）

        Args:
            req: 工作流执行请求参数

        Returns:
            工作流执行结果列表
        """
        session_key = LiveService._get_session_key(req.mid, req.browser_id)
        entry = LiveService.browser_sessions.get(session_key)
        if not entry:
            raise ValueError("浏览器不存在或未运行")

        action_model = await action_crud.get_by_id(req.action_id)
        if not action_model:
            raise ValueError(f"未找到操作: {req.action_id}")

        page = await entry.plugined_session.get_current_page()

        variables = dict(req.variables)
        if req.input_data:
            variables.update(req.input_data)
        if req.output:
            variables["_output_keys"] = req.output

        execution_engine = ExecutionEngine()

        results = await execution_engine._execute_steps(
            req,
            steps=action_model.steps,
            session_id=str(req.browser_id),
            browser_id=str(req.browser_id),
            page=page,
        )

        return results


execution_engine = ExecutionEngine()
