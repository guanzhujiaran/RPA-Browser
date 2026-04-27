"""
执行引擎核心

核心设计:
1. ExecutionEngine 是执行引擎主类
2. 支持执行单个操作或操作流程
3. 自动调用相关插件钩子
4. 支持操作链和并行执行
5. 支持重试和超时控制
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from enum import Enum


# 兼容 Python 3.10 的 StrEnum
class StrEnum(str, Enum):
    """字符串枚举，兼容 Python 3.10"""
    def __str__(self):
        return str(self.value)


import asyncio
import time
import uuid
import copy
from loguru import logger

from app.services.execution.action_registry import (
    ActionRegistry, ActionContext, ActionResult, BaseAction, action_registry
)


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
    plugins: List[str] = field(default_factory=list)  # 启用的插件ID列表


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

    @staticmethod
    def _replace_params_with_context(
        params: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        替换参数中的模板变量

        支持的模板格式：
        - {{step_0_result}} - 引用第0步的完整结果
        - {{step_0_result.content}} - 引用第0步结果的 content 字段
        - {{llm_content}} - 引用上一步返回的 llm_content
        - {{content}} - 引用上一步返回的 content 字段
        """
        import re

        def replace_value(value: Any) -> Any:
            if isinstance(value, str):
                # 替换 {{key}} 或 {{key.field}} 格式
                def replacer(match):
                    template = match.group(1)
                    # 处理嵌套属性访问，如 step_0_result.content
                    if "." in template:
                        parts = template.split(".")
                        result = context.get(parts[0])
                        if result and isinstance(result, dict):
                            for part in parts[1:]:
                                if isinstance(result, dict):
                                    result = result.get(part)
                                else:
                                    return match.group(0)
                        if result is not None:
                            return str(result)
                    else:
                        result = context.get(template)
                        if result is not None:
                            return str(result)
                    return match.group(0)

                return re.sub(r'\{\{(.+?)\}\}', replacer, value)
            elif isinstance(value, dict):
                return {k: replace_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [replace_value(item) for item in value]
            return value

        return replace_value(params)

    async def execute_action(
        self,
        session_id: str,
        browser_id: str,
        page: Any,
        browser: Any,
        action_id: str,
        params: Dict[str, Any],
        plugin_ids: Optional[List[str]] = None,
        user_data: Optional[Dict[str, Any]] = None,
        mid: Optional[str] = None,
    ) -> ActionResult:
        """
        执行单个操作

        Args:
            session_id: 会话ID
            browser_id: 浏览器ID
            page: Playwright Page对象
            browser: Playwright Browser对象
            action_id: 操作ID
            params: 操作参数
            plugin_ids: 要启用的插件ID列表
            user_data: 用户自定义数据
            mid: 用户 mid，用于按需加载用户私有的自定义组合操作

        Returns:
            ActionResult: 操作结果
        """
        start_time = time.time()

        # 创建操作上下文
        ctx = ActionContext(
            session_id=session_id,
            browser_id=browser_id,
            page=page,
            browser=browser,
            params=params,
            user_data=user_data or {},
            plugins=[]
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
                action_id=action_id
            )

        # 验证参数
        valid, error_msg = action.validate_params(params)
        if not valid:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=0,
                action_id=action_id
            )

        try:
            # 执行操作
            result = await action.execute(ctx)
            result.execution_time = time.time() - start_time

            return result

        except asyncio.TimeoutError:
            result = ActionResult(
                success=False,
                error="操作执行超时",
                execution_time=time.time() - start_time,
                action_id=action_id
            )
            return result

        except Exception as e:
            result = ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=action_id
            )
            return result

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
        执行工作流

        Args:
            session_id: 会话ID
            browser_id: 浏览器ID
            page: Playwright Page对象
            browser: Playwright Browser对象
            workflow: 工作流定义
            user_data: 用户数据，可在步骤中通过 {{user.key}} 引用
            mid: 用户 mid，用于按需加载用户私有的自定义组合操作

        Returns:
            List[ActionResult]: 所有步骤的执行结果
        """
        results = []
        task_id = str(uuid.uuid4())

        # 创建任务
        task = ExecutionTask(
            id=task_id,
            session_id=session_id,
            browser_id=browser_id,
            status=ExecutionStatus.RUNNING,
            actions=[{"workflow": workflow.id, "steps": len(workflow.steps)}],
            plugins=[]
        )
        self._tasks[task_id] = task

        logger.info(f"[ExecutionEngine] 开始执行工作流 {workflow.name} (ID: {workflow.id})")

        for i, step in enumerate(workflow.steps):
            task.current_index = i

            # 检查执行条件
            if step.condition:
                prev_result = results[-1] if results else None
                if not step.condition(prev_result):
                    logger.info(f"[ExecutionEngine] 步骤 {i+1} 条件不满足，跳过")
                    continue

            # 处理循环执行
            loop_count = step.loop_count
            loop_while = step.loop_while
            loop_until = step.loop_until
            is_loop = loop_count is not None or loop_while is not None or loop_until is not None

            if is_loop:
                loop_iteration = 0
                max_iterations = loop_count if loop_count else 9999  # 默认最大循环次数

                logger.info(f"[ExecutionEngine] 开始循环步骤 {i+1}，最大迭代: {max_iterations}")

                while loop_iteration < max_iterations:
                    loop_iteration += 1

                    # 构建上下文数据
                    context = dict(user_data) if user_data else {}
                    context["_loop_index"] = loop_iteration - 1  # 0-based
                    context["_loop_count"] = loop_iteration
                    context["_loop_total"] = max_iterations

                    # 添加用户数据，步骤中可通过 {{user.key}} 引用
                    if user_data:
                        context["user"] = user_data
                        for key, value in user_data.items():
                            if key not in context:
                                context[f"user_{key}"] = value

                    for j, result in enumerate(results):
                        if result.success and result.data:
                            context[f"step_{j}_result"] = result.data
                            if isinstance(result.data, dict):
                                for key, value in result.data.items():
                                    if key not in context:
                                        context[key] = value

                    # 检查 loop_while 条件
                    if loop_while:
                        try:
                            # 使用 eval 执行 JS 表达式
                            condition_result = eval(loop_while, {"__builtins__": {}}, context)
                            if not condition_result:
                                logger.info(f"[ExecutionEngine] loop_while 条件不满足，退出循环")
                                break
                        except Exception as e:
                            logger.warning(f"[ExecutionEngine] loop_while 表达式执行失败: {e}")

                    # 检查 loop_until 条件
                    if loop_until:
                        try:
                            condition_result = eval(loop_until, {"__builtins__": {}}, context)
                            if condition_result:
                                logger.info(f"[ExecutionEngine] loop_until 条件满足，退出循环")
                                break
                        except Exception as e:
                            logger.warning(f"[ExecutionEngine] loop_until 表达式执行失败: {e}")

                    logger.debug(f"[ExecutionEngine] 循环步骤 {i+1} 迭代 {loop_iteration}/{max_iterations}")

                    # 替换参数（每次迭代都重新替换，支持使用 _loop_index 等变量）
                    replaced_params = self._replace_params_with_context(step.params, context)

                    # 执行步骤
                    result = await self.execute_action(
                        session_id=session_id,
                        browser_id=browser_id,
                        page=page,
                        browser=browser,
                        action_id=step.action_id,
                        params=replaced_params,
                        plugin_ids=plugin_ids,
                        user_data=user_data,
                        mid=mid,
                    )

                    results.append(result)

                    # 更新 user_data
                    if user_data is not None:
                        user_data[f"step_{i}_loop_{loop_iteration - 1}_result"] = result.data
                        if result.success and result.data:
                            if isinstance(result.data, dict):
                                for key, value in result.data.items():
                                    if key not in user_data:
                                        user_data[key] = value

                    # 循环中某次失败，根据配置处理
                    if not result.success:
                        if workflow.on_error == "stop":
                            logger.warning(f"[ExecutionEngine] 循环中执行失败，停止")
                            break
                        elif workflow.on_error == "continue":
                            logger.warning(f"[ExecutionEngine] 循环中步骤失败，继续")
                            continue

                logger.info(f"[ExecutionEngine] 循环步骤 {i+1} 完成，共迭代 {loop_iteration} 次")

            else:
                # 非循环步骤，正常执行
                # 构建上下文数据
                context = dict(user_data) if user_data else {}

                # 添加用户数据，步骤中可通过 {{user.key}} 引用
                if user_data:
                    context["user"] = user_data
                    for key, value in user_data.items():
                        if key not in context:
                            context[f"user_{key}"] = value

                for j, result in enumerate(results):
                    if result.success and result.data:
                        context[f"step_{j}_result"] = result.data
                        if isinstance(result.data, dict):
                            for key, value in result.data.items():
                                if key not in context:
                                    context[key] = value

                replaced_params = self._replace_params_with_context(step.params, context)

                # 执行步骤
                result = await self.execute_action(
                    session_id=session_id,
                    browser_id=browser_id,
                    page=page,
                    browser=browser,
                    action_id=step.action_id,
                    params=replaced_params,
                    plugin_ids=plugin_ids,
                    user_data=user_data,
                    mid=mid,
                )

                results.append(result)

                # 更新上下文中的数据
                if user_data is not None:
                    user_data[f"step_{i}_result"] = result.data
                    if result.success and result.data:
                        if isinstance(result.data, dict):
                            for key, value in result.data.items():
                                if key not in user_data:
                                    user_data[key] = value
                        if "content" in result.data:
                            user_data["llm_content"] = result.data["content"]
                        if "text" in result.data:
                            user_data["llm_text"] = result.data["text"]

                # 如果失败，根据工作流配置处理
                if not result.success:
                    if workflow.on_error == "stop":
                        logger.warning(f"[ExecutionEngine] 工作流执行失败，停止")
                        break
                    elif workflow.on_error == "continue":
                        logger.warning(f"[ExecutionEngine] 步骤 {i+1} 失败，继续执行")
                        continue

        # 更新任务状态
        task.finished_at = time.time()
        task.total_time = task.finished_at - task.started_at if task.started_at else 0
        task.results = results
        task.status = ExecutionStatus.SUCCESS if all(r.success for r in results) else ExecutionStatus.FAILED

        logger.info(
            f"[ExecutionEngine] 工作流执行完成: {workflow.name}, "
            f"成功 {sum(1 for r in results if r.success)}/{len(results)} 步"
        )

        return results

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
                        plugin_ids=plugin_ids,
                        mid=mid,
                    )

            tasks = [execute_with_semaphore(a) for a in actions]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理异常结果
            processed_results = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    processed_results.append(ActionResult(
                        success=False,
                        error=str(r),
                        action_id=actions[i].get("action_id", "unknown")
                    ))
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
                    plugin_ids=plugin_ids,
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


# 全局执行引擎实例
execution_engine = ExecutionEngine()
