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
from app.services.execution.crud_service import action_crud, plugin_crud, workflow_crud
from app.models.database.workflow.models import (
    ActionMetadata,
    ActionResult,
)
from app.models.execution.action_params import PluginConfig
from app.services.execution.actions.control_flow import CompositeAction as CompositeActionClass
from app.services.execution.action_registry import action_registry
from app.models.execution.request_params import (
    ExecutionRequest,
    ActionExecutionRequest,
    StepExecutionRequest,
    WorkflowExecutionRequest,
    params_to_dict,
)


class Workflow:
    """工作流定义 - 运行时对象"""

    def __init__(
        self,
        id: str,
        name: str,
        description: str = "",
        steps: list | None = None,
        on_error: str = "stop",
    ):
        self.id = id
        self.name = name
        self.description = description
        self.steps = steps or []
        self.on_error = on_error


# ============ 安全评估 ============

def _safe_eval(condition: str, state: Dict[str, Any]) -> bool:
    """
    安全地评估条件表达式
    
    仅允许基础算术运算、比较运算、布尔运算和变量引用
    禁止函数调用、属性访问和危险操作
    """
    # 将 dict 转为 SimpleNamespace 以支持 state.xxx 点号访问
    import types as _types
    state_ns = _types.SimpleNamespace(**state)
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
        
        return bool(eval(compile(tree, "<expr>", "eval"), {}, {"state": state_ns}))
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
        plugins: List[PluginConfig],
        hook_type: str,
        session_id: str,
        browser_id: str,
        page: Page,
        variables: Dict[str, Any] | None = None,
        mid: int = 0,
        action_result: ActionResult | None = None,
    ) -> List[ActionResult]:
        """
        执行指定钩子类型的插件

        Args:
            plugins: 插件配置列表（从 workflow 传入）
            hook_type: 钩子类型
            session_id: 会话ID
            browser_id: 浏览器ID
            page: 页面
            variables: 变量池
            mid: 用户 mid
            action_result: 前一个动作的执行结果（用于 after_action 插件）

        Returns:
            插件执行结果列表
        """
        plugin_results = []

        filtered = [p for p in plugins if p.hook_type == hook_type]
        if not filtered:
            return plugin_results

        for plugin_config in filtered:
            plugin_id = plugin_config.plugin_id
            if not plugin_id:
                continue

            try:
                plugin_info = await plugin_crud.get_by_plugin_id(plugin_id)
                if not plugin_info:
                    logger.warning(f"[Plugin] 插件不存在: {plugin_id}")
                    continue

                if not plugin_info.custom_action_id:
                    logger.warning(f"[Plugin] 插件 {plugin_id} 没有关联自定义动作")
                    continue

                logger.info(
                    f"[Plugin] 执行插件: {plugin_info.name} (hook={hook_type})")

                try:
                    plugin_start_time = time.time()
                    plugin_vars = dict(variables or {})
                    if action_result:
                        plugin_vars["_plugin_action_result"] = {
                            "success": action_result.success,
                            "data": action_result.data,
                            "error": action_result.error,
                        }
                    plugin_vars["_plugin_config"] = plugin_config.config_params

                    plugin_req = ActionExecutionRequest(
                        mid=mid,
                        browser_id=int(browser_id) if browser_id.isdigit() else 0,
                        action_id=plugin_info.custom_action_id,
                        params=plugin_config.config_params,
                        variables=plugin_vars,
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
                        f"[Plugin] 插件 '{plugin_info.name}' 执行完成: "
                        f"success={plugin_result.success}, "
                        f"time={plugin_result.execution_time:.2f}s"
                    )
                except Exception as e:
                    logger.error(
                        f"[Plugin] 插件 '{plugin_info.name}' 执行失败: {e}")
                    plugin_results.append(ActionResult(
                        success=False,
                        error=str(e),
                        execution_time=time.time() - plugin_start_time,
                        action_id=plugin_info.custom_action_id,
                        action_name=f"Plugin: {plugin_info.name}",
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
        plugins: List[PluginConfig] | None = None,
    ) -> ActionResult:
        """
        执行单个操作

        Args:
            req: 操作执行请求参数
            session_id: 会话ID
            browser_id: 浏览器ID
            page: Playwright Page对象（默认页面）
            plugins: 工作流传入的插件列表（仅在 workflow 上下文中传入）

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

        action_class = await action_registry.get_action_class_for_user(req.action_id, req.mid)
        if not action_class:
            return ActionResult(
                success=False,
                error=f"未找到操作: {req.action_id}",
                execution_time=0,
                action_id=req.action_id,
            )

        # 若为自定义复合操作，从 DB 加载 steps 合并到 params
        merged_params = params_to_dict(req.params)
        if issubclass(action_class, CompositeActionClass):
            db_steps = await action_registry.get_custom_action_steps(req.action_id)
            existing_steps = merged_params.get("steps", [])
            if db_steps and (not existing_steps):
                merged_params["steps"] = db_steps

        action: BaseAction = action_class.new_action(
            mid=req.mid,
            page=target_page,
            params=merged_params,
            variables={**req.variables, **(req.input_data if hasattr(req, 'input_data') and req.input_data else {})},
        )

        valid, error_msg = action.validate_params(merged_params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=0, action_id=req.action_id
            )

        try:
            if plugins:
                before_plugins = await self._execute_plugins(
                    plugins=plugins,
                    hook_type="before_action",
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

            if plugins:
                after_plugins = await self._execute_plugins(
                    plugins=plugins,
                    hook_type="after_action",
                    session_id=session_id,
                    browser_id=browser_id,
                    page=target_page,
                    variables=req.variables,
                    mid=req.mid,
                    action_result=result,
                )

                for plugin_result in after_plugins:
                    if not plugin_result.success:
                        logger.warning(
                            f"[Plugin] 后置插件执行失败: {plugin_result.error}")

            result.execution_time = time.time() - start_time
            result.action_id = req.action_id
            result.action_name = action.action_name

            if plugins:
                if result.success:
                    await self._execute_plugins(
                        plugins=plugins,
                        hook_type="on_success",
                        session_id=session_id,
                        browser_id=browser_id,
                        page=target_page,
                        variables=req.variables,
                        mid=req.mid,
                    )
                else:
                    await self._execute_plugins(
                        plugins=plugins,
                        hook_type="on_error",
                        session_id=session_id,
                        browser_id=browser_id,
                        page=target_page,
                        variables=req.variables,
                        mid=req.mid,
                    )

            return result

        except asyncio.TimeoutError:
            if plugins:
                await self._execute_plugins(
                    plugins=plugins,
                    hook_type="on_timeout",
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
            if plugins:
                await self._execute_plugins(
                    plugins=plugins,
                    hook_type="on_error",
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
        plugins: List[PluginConfig] | None = None,
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
            plugins: 工作流关联的插件列表

        Returns:
            执行结果列表
        """
        results = []
        plugins = plugins or []

        for step in steps:
            try:
                if step.condition:
                    condition_result = _safe_eval(step.condition, req.variables)
                    if not condition_result:
                        logger.info(f"跳过步骤 {step.action_id}（条件不满足）")
                        continue

                replaced_params = self._replace_params(step.params, req.variables)

                if step.action_id == "loop" and (step.children or (hasattr(step.params, 'loopBranch') and step.params.loopBranch)):
                    # 从 params 或 BaseWorkflowStep 字段获取 loop_count
                    loop_count = step.loop_count
                    if loop_count is None and hasattr(step.params, 'count'):
                        loop_count = step.params.count
                    loop_children = step.children
                    if not loop_children and hasattr(step.params, 'loopBranch') and step.params.loopBranch:
                        loop_children = step.params.loopBranch

                    if loop_count is not None:
                        for i in range(loop_count):
                            req.variables["loop_index"] = i
                            loop_results = await self._execute_steps(
                                req, steps=loop_children, session_id=session_id, browser_id=browser_id, page=page,
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

                elif step.action_id == "if_else" and (step.children or (hasattr(step.params, 'TrueBranch') and (step.params.TrueBranch or step.params.FalseBranch))):
                    condition = (
                        step.params.condition
                        if hasattr(step.params, 'condition')
                        else step.params.get("condition", "")
                    )
                    condition_result = _safe_eval(condition, req.variables)

                    # 从 params 获取分支（优先），否则从 children 获取
                    if hasattr(step.params, 'TrueBranch'):
                        true_branch = list(step.params.TrueBranch) if step.params.TrueBranch else []
                        false_branch = list(step.params.FalseBranch) if step.params.FalseBranch else []
                    else:
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
                        plugins=plugins,
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
        params: dict[str, Any],
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """
        替换参数中的模板变量

        支持 {{variable_name}} 格式的变量替换。
        若 params 是 Pydantic 模型，先转为 dict 再处理。
        """
        if not isinstance(params, dict):
            if hasattr(params, 'model_dump'):
                params = params.model_dump()
            else:
                return params

        def _replace_value(val: Any) -> Any:
            if isinstance(val, str):
                return re.sub(r"\{\{([\w.]+)\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), val)
            elif isinstance(val, dict):
                return {k: _replace_value(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [_replace_value(v) for v in val]
            return val

        return _replace_value(params)

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
        metadata = action_registry.get_action_metadata(req.action_id)
        if not metadata:
            raise ValueError(f"未找到操作: {req.action_id}")

        # 检测是否为复合操作，steps 现在从 params 中获取
        if issubclass(action_class, CompositeActionClass):
            params_dict = params_to_dict(req.params)
            steps = params_dict.get("steps", [])
            if req.step_index < 0 or req.step_index >= len(steps):
                raise ValueError(f"步骤索引 {req.step_index} 超出范围 (0-{len(steps)-1})")

            step = steps[req.step_index]
            step_params = execution_engine._replace_params(
                step.get("params", {}), req.variables)

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
        *,
        page: Page | None = None,
    ) -> List[ActionResult]:
        """
        🔑 Service 层方法：执行工作流（自动管理会话和页面）

        Args:
            req: 工作流执行请求参数
            page: 可选，直接传入页面对象（用于测试），绕过 LiveService

        Returns:
            工作流执行结果列表
        """
        if page is None:
            session_key = LiveService._get_session_key(req.mid, req.browser_id)
            entry = LiveService.browser_sessions.get(session_key)
            if not entry:
                raise ValueError("浏览器不存在或未运行")
            page = await entry.plugined_session.get_current_page()

        action_model = await action_crud.get_by_action_id(req.action_id)
        if not action_model:
            raise ValueError(f"未找到操作: {req.action_id}")

        variables = dict(req.variables)
        if req.input_data:
            variables.update(req.input_data)
        if req.output:
            variables["_output_keys"] = req.output

        # 获取工作流关联的插件
        plugins = []
        if req.workflow_id:
            plugins = await workflow_crud.get_enabled_plugins(req.workflow_id)

        # 将 steps 从 JSON 反序列化时可能仍是 dict，需要转为 WorkflowStep 对象
        from app.models.execution.action_params import _ensure_action_type, workflow_step_adapter
        normalized_steps: list[WorkflowStep] = []
        for s in action_model.steps:
            if isinstance(s, dict):
                s = workflow_step_adapter.validate_python(_ensure_action_type(s))
            normalized_steps.append(s)

        execution_engine = ExecutionEngine()

        results = await execution_engine._execute_steps(
            req,
            steps=normalized_steps,
            session_id=str(req.browser_id),
            browser_id=str(req.browser_id),
            page=page,
            plugins=plugins,
        )

        return results


execution_engine = ExecutionEngine()
