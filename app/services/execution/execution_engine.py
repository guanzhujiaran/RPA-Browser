import sys

# Python 3.10 兼容性：StrEnum 在 3.11+ 中引入
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum
    class StrEnum(str, Enum):
        """Python 3.10 兼容的 StrEnum"""
        pass

"""
执行引擎核心

核心设计:
1. ExecutionEngine 是执行引擎主类
2. 支持执行单个操作或操作流程
3. 自动调用相关插件钩子
4. 支持操作链和并行执行
5. 支持重试和超时控制
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable

import asyncio
import time
import uuid
import copy
from loguru import logger

from app.services.execution.action_registry import (
    ActionRegistry,
    ActionContext,
    ActionResult,
    BaseAction,
    action_registry,
)
from app.services.RPA_browser.live_service import LiveService
from app.config import settings
from app.models.core.workflow.models import ActionPluginLink, UserPluginModel, CustomActionModel
from app.services.execution.crud_service import action_crud, plugin_crud


class ExecutionStatus(StrEnum):
    """执行状态"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ExecutionTask:
    """执行任务"""

    id: str
    session_id: str
    browser_id: str
    status: ExecutionStatus
    actions: List[Dict[str, Any]]  # 操作列表
    current_index: int = 0
    results: List[ActionResult] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total_time: float = 0.0
    error: Optional[str] = None


@dataclass
class WorkflowStep:
    """工作流步骤"""

    action_id: str
    params: Dict[str, Any]
    retry: int = 0
    condition: Optional[Callable[[ActionResult], bool]] = None  # 执行条件
    # 循环配置
    loop_count: Optional[int] = None  # 循环次数，如 loop_count=3 表示执行3次
    loop_while: Optional[str] = None  # JS表达式，返回true时继续循环
    loop_until: Optional[str] = None  # JS表达式，返回true时退出循环


@dataclass
class Workflow:
    """工作流定义"""

    id: str
    name: str
    description: str = ""
    steps: List[WorkflowStep] = field(default_factory=list)
    on_error: str = "stop"  # stop, continue, rollback


class ExecutionEngine:
    """
    执行引擎

    负责执行浏览器自动化操作和流程
    """

    def __init__(self):
        self._tasks: Dict[str, ExecutionTask] = {}
        self._action_registry = action_registry
        self._running = True

    def _replace_params_with_context(
        self, params: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        替换参数中的模板变量（增强版）

        支持的模板格式：
        - {{state.loop.current_item}} - 引用当前循环项
        - {{state.llm_reply}} - 引用之前存入 state 的变量
        - {{step_0_result.content}} - 引用历史步骤结果
        """

        def replace_value(value: Any) -> Any:
            if isinstance(value, str):
                # 替换 {{key}} 或 {{key.sub_key}} 格式
                def replacer(match):
                    template = match.group(1)
                    parts = template.split(".")

                    # 尝试从 context (即 user_data) 中查找
                    current = context
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

    async def _execute_plugins(
        self,
        hook_type: str,
        action_id: str,
        ctx: ActionContext,
        mid: Optional[str] = None,
        result: Optional[ActionResult] = None,
        error: Optional[Exception] = None,
    ) -> List[ActionResult]:
        """
        执行指定钩子类型的插件
        
        Args:
            hook_type: 钩子类型 (before_action, after_action, on_success, on_error)
            action_id: 当前执行的动作 ID
            ctx: 动作上下文
            mid: 用户 ID
            result: 动作执行结果（用于 after_action/on_success/on_error）
            error: 异常信息（用于 on_error）
            
        Returns:
            List[ActionResult]: 插件执行结果列表
        """
        if not mid:
            return []
        
        plugin_results = []
        
        try:
            # 1. 查询该动作关联的插件
            enabled_plugins = await action_crud.get_enabled_plugins(action_id)
            
            if not enabled_plugins:
                logger.debug(f"[Plugin] 动作 {action_id} 没有关联的插件")
                return []
            
            logger.info(f"[Plugin] 找到 {len(enabled_plugins)} 个关联插件")
            
            # 2. 遍历每个插件配置
            for plugin_config in enabled_plugins:
                plugin_id = plugin_config.get("plugin_id")
                config_params = plugin_config.get("config_params", {})
                
                if not plugin_id:
                    continue
                
                # 3. 查询插件详情
                plugin_model = await plugin_crud.get_by_plugin_id(plugin_id)
                if not plugin_model or not plugin_model.is_enabled:
                    logger.warning(f"[Plugin] 插件 {plugin_id} 不存在或已禁用")
                    continue
                
                # 4. 检查钩子类型是否匹配
                if plugin_model.hook_type != hook_type:
                    continue
                
                # 5. 获取插件关联的自定义动作
                custom_action_id = plugin_model.custom_action_id
                if not custom_action_id:
                    logger.warning(f"[Plugin] 插件 {plugin_id} 没有关联自定义动作")
                    continue
                
                logger.info(f"[Plugin] 执行插件: {plugin_model.name} (hook={hook_type})")
                
                # 6. 创建插件动作上下文
                plugin_ctx = ActionContext(
                    session_id=ctx.session_id,
                    browser_id=ctx.browser_id,
                    page=ctx.page,
                    browser=ctx.browser,
                    params=config_params,  # 使用插件的配置参数
                    user_data=ctx.user_data,
                )
                
                # 7. 执行插件关联的自定义动作
                try:
                    plugin_start_time = time.time()
                    plugin_result = await self.execute_action(
                        session_id=ctx.session_id,
                        browser_id=ctx.browser_id,
                        page=ctx.page,
                        browser=ctx.browser,
                        action_id=custom_action_id,
                        params=config_params,
                        user_data=ctx.user_data,
                        mid=mid,
                    )
                    plugin_result.execution_time = time.time() - plugin_start_time
                    plugin_results.append(plugin_result)
                    
                    logger.info(
                        f"[Plugin] 插件 '{plugin_model.name}' 执行完成: "
                        f"success={plugin_result.success}, "
                        f"time={plugin_result.execution_time:.2f}s"
                    )
                except Exception as e:
                    logger.error(f"[Plugin] 插件 '{plugin_model.name}' 执行失败: {e}")
                    plugin_results.append(ActionResult(
                        success=False,
                        error=str(e),
                        execution_time=time.time() - plugin_start_time,
                        action_id=custom_action_id,
                        action_name=f"Plugin: {plugin_model.name}",
                    ))
        
        except Exception as e:
            logger.error(f"[Plugin] 加载插件配置失败: {e}")
        
        return plugin_results

    async def execute_action(
        self,
        session_id: str,
        browser_id: str,
        page: Any,
        browser: Any,
        action_id: str,
        params: Dict[str, Any],
        user_data: Optional[Dict[str, Any]] = None,
        mid: Optional[str] = None,
        page_index: Optional[int] = None,  # 🔑 新增：页面索引参数
    ) -> ActionResult:
        """
        执行单个操作

        Args:
            session_id: 会话ID
            browser_id: 浏览器ID
            page: Playwright Page对象（默认页面）
            browser: Playwright BrowserContext对象
            action_id: 操作ID
            params: 操作参数
            user_data: 用户自定义数据
            mid: 用户 mid，用于按需加载用户私有的自定义组合操作
            page_index: 🔑 页面索引，如果提供则切换到指定页面执行操作

        Returns:
            ActionResult: 操作结果
        """
        start_time = time.time()

        # 🔑 如果指定了 page_index，需要获取对应的页面
        target_page = page
        if page_index is not None:
            session_key = LiveService._get_session_key(
                int(session_id.split("_")[0]) if "_" in session_id else int(session_id),
                int(browser_id),
            )

            if session_key in LiveService.browser_sessions:
                entry = LiveService.browser_sessions[session_key]
                all_pages = await entry.plugined_session.get_all_pages()

                if 0 <= page_index < len(all_pages):
                    target_page = all_pages[page_index]
                    logger.info(f"📄 使用页面索引 {page_index}: {target_page.url}")
                else:
                    return ActionResult(
                        success=False,
                        error=f"页面索引 {page_index} 超出范围 (0-{len(all_pages)-1})",
                        execution_time=0,
                        action_id=action_id,
                    )
            else:
                logger.warning(f"会话 {session_key} 不存在，使用默认页面")

        # 创建操作上下文
        ctx = ActionContext(
            session_id=session_id,
            browser_id=browser_id,
            page=target_page,  # 🔑 使用目标页面
            browser=browser,
            params=params,
            user_data=user_data or {},
        )

        # 获取操作实例：优先系统级，再按 mid 按需加载用户私有操作
        if mid:
            action = await self._action_registry.create_action_for_user(action_id, mid)
        else:
            action = self._action_registry.create_action(action_id)
        if not action:
            return ActionResult(
                success=False,
                error=f"未找到操作: {action_id}",
                execution_time=0,
                action_id=action_id,
            )

        # 验证参数
        valid, error_msg = action.validate_params(params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=0, action_id=action_id
            )

        try:
            # ========== 1. 执行 before_action 插件 ==========
            if mid:
                before_plugins = await self._execute_plugins(
                    hook_type="before_action",
                    action_id=action_id,
                    ctx=ctx,
                    mid=mid,
                )
                if before_plugins:
                    logger.info(f"[Plugin] before_action 插件执行完成: {len(before_plugins)} 个")
            
            # ========== 2. 执行主动作 ==========
            logger.info(f"[ExecutionEngine] 正在执行操作 {action_id} (ID: {action_id})")
            result = await action.execute(ctx)
            result.execution_time = time.time() - start_time
            
            # 记录执行日志到 ActionResult
            if hasattr(action, 'logs') and action.logs:
                result.logs.extend(action.logs)
            result.logs.append(f"Action '{action_id}' executed in {result.execution_time:.2f}s")
            
            # ========== 3. 执行 after_action / on_success / on_error 插件 ==========
            if mid:
                # 根据执行结果决定触发哪个钩子
                if result.success:
                    # 执行成功：触发 after_action 和 on_success
                    after_plugins = await self._execute_plugins(
                        hook_type="after_action",
                        action_id=action_id,
                        ctx=ctx,
                        mid=mid,
                        result=result,
                    )
                    success_plugins = await self._execute_plugins(
                        hook_type="on_success",
                        action_id=action_id,
                        ctx=ctx,
                        mid=mid,
                        result=result,
                    )
                    if after_plugins or success_plugins:
                        logger.info(
                            f"[Plugin] 成功钩子插件执行完成: "
                            f"after={len(after_plugins)}, success={len(success_plugins)}"
                        )
                else:
                    # 执行失败：触发 after_action 和 on_error
                    after_plugins = await self._execute_plugins(
                        hook_type="after_action",
                        action_id=action_id,
                        ctx=ctx,
                        mid=mid,
                        result=result,
                    )
                    error_plugins = await self._execute_plugins(
                        hook_type="on_error",
                        action_id=action_id,
                        ctx=ctx,
                        mid=mid,
                        result=result,
                        error=Exception(result.error),
                    )
                    if after_plugins or error_plugins:
                        logger.info(
                            f"[Plugin] 失败钩子插件执行完成: "
                            f"after={len(after_plugins)}, error={len(error_plugins)}"
                        )
            
            return result

        except asyncio.TimeoutError:
            result = ActionResult(
                success=False,
                error="操作执行超时",
                execution_time=time.time() - start_time,
                action_id=action_id,
                logs=[f"Action '{action_id}' timed out after {time.time() - start_time:.2f}s"]
            )
            return result

        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"[ExecutionEngine] 操作 {action_id} 执行异常: {e}\n{error_traceback}")
            result = ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=action_id,
                logs=[f"Action '{action_id}' failed with exception: {str(e)}", f"Traceback:\n{error_traceback}"]
            )
            return result

    async def _execute_steps(
        self, steps: List["WorkflowStep"], ctx: ActionContext
    ) -> List[ActionResult]:
        """
        递归执行步骤列表（带循环引用检测）
        
        防护机制：
        1. 嵌套深度限制（防止无限递归）
        2. 执行栈追踪（检测循环引用）
        3. 调用链记录（便于调试）
        """
        # 检查并增加递归深度
        current_depth = ctx.user_data.get("_recursion_depth", 0)
        max_depth = settings.workflow_max_nesting_depth
        
        if current_depth >= max_depth:
            logger.error(f"递归深度超过限制 ({current_depth}/{max_depth})")
            return [ActionResult(
                success=False,
                error=f"递归深度超过限制 ({current_depth}/{max_depth})，请简化工作流结构",
                execution_time=0,
                action_id="workflow",
                action_name="工作流执行",
            )]
        
        # 初始化或获取执行栈（用于检测循环引用）
        execution_stack = ctx.user_data.setdefault("_execution_stack", [])
        
        # 增加递归深度
        ctx.user_data["_recursion_depth"] = current_depth + 1
        
        results = []
        try:
            for step in steps:
                # 🔒 循环引用检测：检查是否已经在执行栈中
                step_key = f"{step.action_id}@{id(step)}"
                if step.action_id in execution_stack:
                    cycle_path = " → ".join(execution_stack + [step.action_id])
                    error_msg = f"检测到循环引用: {cycle_path}"
                    logger.error(f"🚫 {error_msg}")
                    return [ActionResult(
                        success=False,
                        error=error_msg,
                        execution_time=0,
                        action_id=step.action_id,
                        action_name=f"步骤 {step.action_id}",
                        logs=[f"循环引用检测失败，执行栈: {execution_stack}"]
                    )]
                
                # 将当前步骤加入执行栈
                execution_stack.append(step.action_id)
                
                try:
                    # 1. 条件判断
                    if step.condition:
                        state = ctx.user_data.get("state", {})
                        try:
                            # 允许访问 state 中的变量
                            if not eval(step.condition, {"__builtins__": {}}, {"state": state}):
                                logger.info(f"跳过步骤 {step.action_id}: 条件不满足")
                                continue
                        except Exception as e:
                            logger.warning(f"条件评估失败: {e}")
                            continue

                    # 2. 准备参数（增强版替换）
                    replaced_params = self._replace_params_with_context(
                        step.params, ctx.user_data
                    )

                    # 3. 处理控制流动作 (Loop / IfElse)
                    if step.action_id == "loop":
                        replaced_params["_children_steps"] = step.children or []
                        replaced_params["_execute_steps_func"] = self._execute_steps
                    elif step.action_id == "if_else":
                        # 将子步骤按分支分类，这里假设 children[0] 是 true, children[1] 是 false
                        if step.children:
                            replaced_params["_true_branch_steps"] = (
                                step.children[0].children if len(step.children) > 0 else []
                            )
                            replaced_params["_false_branch_steps"] = (
                                step.children[1].children if len(step.children) > 1 else []
                            )
                        replaced_params["_execute_steps_func"] = self._execute_steps

                    # 4. 执行当前步骤
                    result = await self.execute_action(
                        session_id=ctx.session_id,
                        browser_id=ctx.browser_id,
                        page=ctx.page,
                        browser=ctx.browser,
                        action_id=step.action_id,
                        params=replaced_params,
                        user_data=ctx.user_data,
                        mid=ctx.user_data.get("mid"),
                    )
                    results.append(result)

                    # 5. 更新状态
                    if step.output_var and result.success:
                        ctx.user_data.setdefault("state", {})[step.output_var] = result.data

                    if result.success and isinstance(result.data, dict):
                        ctx.user_data.setdefault("state", {}).update(result.data)

                    # 6. 错误处理
                    if not result.success:
                        # 简单起见，遇到错误停止当前分支
                        break
                finally:
                    # 从执行栈中移除当前步骤（无论成功与否）
                    if execution_stack and execution_stack[-1] == step.action_id:
                        execution_stack.pop()
        finally:
            # 恢复递归深度
            ctx.user_data["_recursion_depth"] = current_depth

        return results

    async def execute_workflow(
        self,
        session_id: str,
        browser_id: str,
        page: Any,
        browser: Any,
        workflow: Workflow,
        user_data: Optional[Dict[str, Any]] = None,
        mid: Optional[str] = None,
    ) -> List[ActionResult]:
        """
        执行工作流（重构版：支持嵌套和状态管理）
        """
        # 初始化执行上下文
        ctx = ActionContext(
            session_id=session_id,
            browser_id=browser_id,
            page=page,
            browser=browser,
            params={},
            user_data=user_data or {},
        )

        # 注入 mid 以便子步骤加载操作
        ctx.user_data["mid"] = mid
        
        # 初始化递归深度
        ctx.user_data.setdefault("_recursion_depth", 0)

        # 将 workflow.steps 转换为新的 WorkflowStep 模型并执行
        return await self._execute_steps(workflow.steps, ctx)

    async def execute_batch(
        self,
        session_id: str,
        browser_id: str,
        page: Any,
        browser: Any,
        actions: List[Dict[str, Any]],
        plugin_ids: Optional[List[str]] = None,
        parallel: bool = False,
        max_concurrent: int = 3,
        mid: Optional[str] = None,
    ) -> List[ActionResult]:
        """
        批量执行操作

        Args:
            session_id: 会话ID
            browser_id: 浏览器ID
            page: Playwright Page对象
            browser: Playwright Browser对象
            actions: 操作列表 [{"action_id": "click", "params": {...}}, ...]
            plugin_ids: 启用的插件ID列表
            parallel: 是否并行执行
            max_concurrent: 最大并发数
            mid: 用户 mid，用于按需加载用户私有的自定义组合操作

        Returns:
            List[ActionResult]: 执行结果列表
        """
        if parallel:
            # 并行执行
            semaphore = asyncio.Semaphore(max_concurrent)

            async def execute_with_semaphore(action: Dict[str, Any]) -> ActionResult:
                async with semaphore:
                    return await self.execute_action(
                        session_id=session_id,
                        browser_id=browser_id,
                        page=page,
                        browser=browser,
                        action_id=action["action_id"],
                        params=action.get("params", {}),
                        mid=mid,
                    )

            tasks = [execute_with_semaphore(a) for a in actions]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理异常结果
            processed_results = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    processed_results.append(
                        ActionResult(
                            success=False,
                            error=str(r),
                            action_id=actions[i].get("action_id", "unknown"),
                        )
                    )
                else:
                    processed_results.append(r)

            return processed_results
        else:
            # 顺序执行
            results = []
            for action in actions:
                result = await self.execute_action(
                    session_id=session_id,
                    browser_id=browser_id,
                    page=page,
                    browser=browser,
                    action_id=action["action_id"],
                    params=action.get("params", {}),
                    mid=mid,
                )
                results.append(result)

                # 如果失败，可以选择停止或继续
                if not result.success:
                    logger.warning(f"[ExecutionEngine] 操作 {action['action_id']} 失败")

            return results

    def get_task(self, task_id: str) -> Optional[ExecutionTask]:
        """获取任务信息"""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> List[ExecutionTask]:
        """获取所有任务"""
        return list(self._tasks.values())

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self._tasks.get(task_id)
        if task and task.status == ExecutionStatus.RUNNING:
            task.status = ExecutionStatus.CANCELLED
            task.finished_at = time.time()
            return True
        return False

    @staticmethod
    async def execute_action_with_session(
        mid: int,
        browser_id: int,
        action_id: str,
        params: Dict[str, Any],
        user_data: Optional[Dict[str, Any]] = None,
        page_index: Optional[int] = None,
    ) -> ActionResult:
        """
        🔑 Service 层方法：执行操作（自动管理会话和页面）

        Args:
            mid: 用户ID
            browser_id: 浏览器ID
            action_id: 操作ID
            params: 操作参数
            user_data: 用户自定义数据
            page_index: 页面索引，指定在哪个 tab 页执行

        Returns:
            ActionResult: 执行结果
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return ActionResult(
                success=False,
                error=f"浏览器会话不存在: {session_key}",
                execution_time=0,
                action_id=action_id,
            )

        # 获取页面
        if page_index is not None:
            all_pages = await entry.plugined_session.get_all_pages()
            if 0 <= page_index < len(all_pages):
                page = all_pages[page_index]
            else:
                return ActionResult(
                    success=False,
                    error=f"页面索引 {page_index} 超出范围 (0-{len(all_pages)-1})",
                    execution_time=0,
                    action_id=action_id,
                )
        else:
            page = await entry.plugined_session.get_current_page()

        # 获取 browser context
        browser = entry.plugined_session.browser_context

        # 执行操作
        return await execution_engine.execute_action(
            session_id=str(browser_id),
            browser_id=str(browser_id),
            page=page,
            browser=browser,
            action_id=action_id,
            params=params,
            user_data=user_data,
            mid=str(mid),
            page_index=None,  # 已经在上面处理了页面选择
        )

    @staticmethod
    async def execute_action_step_with_session(
        mid: int,
        browser_id: int,
        action_id: str,
        params: Dict[str, Any],
        step_index: int = 0,
        user_data: Optional[Dict[str, Any]] = None,
        page_index: Optional[int] = None,
    ) -> tuple:
        """
        🔑 Service 层方法：单步执行操作（自动管理会话和页面）

        Args:
            mid: 用户ID
            browser_id: 浏览器ID
            action_id: 操作ID
            params: 操作参数
            step_index: 步骤索引（如果是复合操作）
            user_data: 用户自定义数据
            page_index: 页面索引，指定在哪个 tab 页执行操作

        Returns:
            tuple: (step_index, action_id, action_name, ActionResult)
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)
        if not entry:
            raise ValueError("浏览器不存在或未运行")

        # 根据 page_index 选择页面
        if page_index is not None:
            all_pages = await entry.plugined_session.get_all_pages()
            if page_index < 0 or page_index >= len(all_pages):
                raise ValueError(
                    f"页面索引 {page_index} 超出范围 (0-{len(all_pages)-1})"
                )
            page = all_pages[page_index]
        else:
            page = await entry.plugined_session.get_current_page()

        browser = entry.plugined_session.browser_context.browser

        # 从数据库加载操作
        action_instance = await action_registry.create_action_for_user(
            action_id, str(mid)
        )
        metadata = action_registry.get_action_metadata(action_id)
        if not metadata:
            raise ValueError(f"未找到操作: {action_id}")

        # 检查是否为组合操作
        composite = action_instance if hasattr(action_instance, "_steps") else None

        if composite and hasattr(composite, "_steps"):
            # 组合操作：执行指定步骤
            steps = composite._steps
            if step_index < 0 or step_index >= len(steps):
                raise ValueError(f"步骤索引 {step_index} 超出范围 (0-{len(steps)-1})")

            step = steps[step_index]
            # 替换参数
            step_params = composite._replace_params(step.get("params", {}), params)

            # 执行子操作
            result = await execution_engine.execute_action(
                session_id=str(browser_id),
                browser_id=str(browser_id),
                page=page,
                browser=browser,
                action_id=step["action_id"],
                params=step_params,
                mid=str(mid),
                user_data=user_data,  # 透传 user_data
                page_index=None,  # 已经在上面处理了页面选择
            )

            return (step_index, step["action_id"], metadata.name, result)
        else:
            # 普通操作：直接执行
            result = await execution_engine.execute_action(
                session_id=str(browser_id),
                browser_id=str(browser_id),
                page=page,
                browser=browser,
                action_id=action_id,
                params=params,
                mid=str(mid),
                user_data=user_data,  # 透传 user_data
                page_index=None,  # 已经在上面处理了页面选择
            )

            return (0, action_id, metadata.name, result)
    @staticmethod
    async def execute_workflow_with_session(
        mid: int,
        browser_id: int,
        workflow: Any,  # Workflow 对象
        user_data: Optional[Dict[str, Any]] = None,
        page_index: Optional[int] = None,
    ) -> List[ActionResult]:
        """
        🔑 Service 层方法：执行工作流（自动管理会话和页面）

        Args:
            mid: 用户ID
            browser_id: 浏览器ID
            workflow: Workflow 对象
            user_data: 用户自定义数据
            page_index: 页面索引，指定在哪个 tab 页执行操作

        Returns:
            List[ActionResult]: 执行结果列表
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)
        if not entry:
            raise ValueError("浏览器不存在或未运行")

        # 根据 page_index 选择页面
        if page_index is not None:
            all_pages = await entry.plugined_session.get_all_pages()
            if page_index < 0 or page_index >= len(all_pages):
                raise ValueError(
                    f"页面索引 {page_index} 超出范围 (0-{len(all_pages)-1})"
                )
            page = all_pages[page_index]
        else:
            page = await entry.plugined_session.get_current_page()

        browser = entry.plugined_session.browser_context.browser

        # 执行工作流
        results = await execution_engine.execute_workflow(
            session_id=str(browser_id),
            browser_id=str(browser_id),
            page=page,
            browser=browser,
            workflow=workflow,
            user_data=user_data,
            mid=str(mid),
        )

        return results

    @staticmethod
    async def execute_batch_with_session(
        mid: int,
        browser_id: int,
        actions: List[Dict[str, Any]],
        parallel: bool = False,
        user_data: Optional[Dict[str, Any]] = None,
        page_index: Optional[int] = None,
    ) -> List[ActionResult]:
        """
        🔑 Service 层方法：批量执行操作（自动管理会话和页面）

        Args:
            mid: 用户ID
            browser_id: 浏览器ID
            actions: 操作列表，每个元素包含 action_id 和 params
            parallel: 是否并行执行
            user_data: 共享自定义数据
            page_index: 页面索引，指定在哪个 tab 页执行操作

        Returns:
            List[ActionResult]: 执行结果列表
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)
        if not entry:
            raise ValueError("浏览器不存在或未运行")

        # 根据 page_index 选择页面
        if page_index is not None:
            all_pages = await entry.plugined_session.get_all_pages()
            if page_index < 0 or page_index >= len(all_pages):
                raise ValueError(
                    f"页面索引 {page_index} 超出范围 (0-{len(all_pages)-1})"
                )
            page = all_pages[page_index]
        else:
            page = await entry.plugined_session.get_current_page()

        browser = entry.plugined_session.browser_context.browser

        # 批量执行
        return await execution_engine.execute_batch(
            session_id=str(browser_id),
            browser_id=str(browser_id),
            page=page,
            browser=browser,
            actions=actions,
            parallel=parallel,
            mid=str(mid),
            user_data=user_data,  # 透传 user_data
        )


# 全局执行引擎实例
execution_engine = ExecutionEngine()
