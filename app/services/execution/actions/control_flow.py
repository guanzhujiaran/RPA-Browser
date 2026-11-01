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
from app.models.execution.action_params import BuiltinActionName
from app.models.execution.action_params import is_composite_action_id
from app.models.execution.action_params import IfElseParams, IfElseResult
from app.models.execution.action_params import ParallelParams
from app.models.execution.action_params import LoopParams, LoopResult
from app.models.execution.action_params import (
    BaseWorkflowStep,
    WorkflowStep,
    workflow_step_adapter,
    _ensure_action_type,
)
from app.models.execution.condition_models import (
    ConditionRule,
    evaluate_rule,
    evaluate_condition,
    ConditionEvaluateError,
)
import ast
import asyncio
import time
import re
import uuid
from typing import Any, Dict, Iterator, List
from sqlalchemy import select
from botright.playwright_mock import Page
from loguru import logger
from app.services.execution.actions.base import (
    ActionResult,
    BaseAction,
    get_action_class,
    get_custom_action_resolver,
    register_action_class,
)
from app.services.execution.actions.safe_eval import (
    _eval_ast_node,
    safe_evaluate_condition,
)
from app.config import settings
from app.services.RPA_browser.session.page_registry import page_registry
from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.builtin_action_types import OnErrorEnum
from app.models.database.log.models import ActionLogSourceEnum
from app.services.execution.action_logger import (
    ActionLogContext,
    save_action_log,
    resolve_log_option,
)
from app.services.execution.crud_service import action_crud_svr
from app.utils.depends.session_manager import DatabaseSessionManager


class ExecutionContext:
    """执行上下文 - 保存执行状态（备忘录模式）"""

    def __init__(
        self,
        variables: dict | None = None,
        exec_meta: dict | None = None,
    ):
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
            "execution_stack": list(self.execution_stack),
            "current_depth": self.current_depth,
        }

    def restore_state(self, state: Dict):
        """恢复执行栈与嵌套深度。

        注意：`variables` 是跨步骤共享的变量池，循环体 / 分支体内新写入的变量
        （例如 if_else 分支里 llm 产出的 `comment_text`）必须保留给后续步骤使用，
        因此这里**不回滚 variables**，否则下游步骤只会拿到 `{{变量}}` 字面量。
        """
        self.execution_stack = state["execution_stack"]
        self.current_depth = state["current_depth"]

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
                action_name=action_id,
            )

        # 获取执行策略
        strategy = self._get_strategy(action_id)
        return await strategy.execute(step, step_index)

    async def execute_with_retry(
        self, step: WorkflowStep, step_index: int = 0
    ) -> list[ActionResult]:
        """执行单个步骤并按 ``step.retry`` 重试，返回全部尝试结果。

        与顶层责任链（``CompositeAction._execute_chain``）的重试语义一致：
        任一次尝试成功即停止；重试耗尽仍失败则返回全部结果，由调用方
        中断后续步骤并向上传播失败。对循环 / 条件判断等控制流步骤同样生效
        （顶层责任链本就如此，循环体 / 分支内的子步骤不应有差异）。

        旧实现中循环体 / 条件分支内的子步骤直接调用 ``execute()``，
        步骤 JSON 上声明的 ``retry`` 被静默忽略，且失败后仍继续执行
        后续步骤——这正是「报错了却定位不到、甚至谎报成功」的根因之一。

        **重试全部失败后才执行兜底分支（``on_error_branch``）**：与工作流执行
        链路（``pipeline.py``）语义对齐——之前复合动作路径完全不解析
        ``on_error_branch``，用户在步骤 JSON 里配的「失败回退 / 告警」永远
        不执行，而同一步骤在工作流里跑却会执行，两条路径行为不一致。
        """
        results = [await self.execute(step, step_index)]
        if not results[0].success and step.retry > 0:
            for _ in range(step.retry):
                results.append(await self.execute(step, step_index))
                if results[-1].success:
                    break
        if not results[-1].success:
            results.extend(await self._run_error_branch(step, results[-1]))
        return results

    async def _run_error_branch(
        self, step: WorkflowStep, failed: ActionResult
    ) -> list[ActionResult]:
        """执行步骤声明的兜底分支（``on_error_branch``）

        兜底分支里的步骤按普通步骤执行（**不再递归触发自己的兜底分支**，
        避免兜底链无限嵌套）。分支内任一步失败即中断该分支。
        """
        branch_steps = list(getattr(step, "on_error_branch", None) or [])
        if not branch_steps:
            return []
        self.context.variables["last_error"] = failed.error or "未知错误"
        self.context.variables["last_output"] = failed.data
        logger.info(f"执行 {step.action_id} 的错误处理分支")
        results: list[ActionResult] = []
        for sub_step in branch_steps:
            sub_result = await self.execute(sub_step)
            results.append(sub_result)
            if not sub_result.success:
                break
        return results

    def _get_strategy(self, action_id: str) -> "ExecutionStrategy":
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

    def _eval_node(self, node: ast.AST, variables: dict | None = None) -> Any:
        """委托给模块级 _eval_ast_node"""
        if variables is None:
            variables = self.context.variables
        return _eval_ast_node(node, variables)


class AtomicStrategy(ExecutionStrategy):
    """原子操作执行策略"""

    async def execute(self, step: WorkflowStep, step_index: int = 0) -> ActionResult:
        """执行原子操作"""
        action_id = step.action_id
        params = step.params or {}

        # 替换模板变量（严格模式：存在未解析变量即失败）。
        # 该步骤在 try 之外执行，异常需在此捕获并补步骤上下文，
        # 否则只会泛泛冒泡到复合层，丢失「哪个步骤、哪个变量」信息。
        try:
            params = self._replace_templates(params)
        except Exception as e:  # noqa: BLE001
            error_result = ActionResult(
                success=False,
                error=f"步骤执行失败({action_id}): {e}",
                action_id=action_id,
                action_name=action_id,
                replaced_params=params
                if isinstance(params, dict)
                else getattr(params, "model_dump", lambda: {})() or {},
            )
            log_ctx = self._new_log_context(
                action_id, params, self.context.current_depth, action_id, log_config=None
            )
            await save_action_log(log_ctx, error_result)
            return error_result

        # 解析生效的日志采集配置：自定义操作按其基础配置，内置操作回落服务端兜底；
        # 子步骤继承父操作透传下来的采集配置（也可被自身参数中的 log 选项覆盖）
        substep_cfg = await resolve_log_option(
            self.mid, action_id, params, self.context.log_config
        )

        # 确定日志用的 action_type（自定义复合动作映射为 composite）
        log_action_type = (
            BuiltinActionType.COMPOSITE
            if is_composite_action_id(action_id)
            else action_id
        )
        # 嵌套深度用执行上下文的 current_depth（loop/if_else 进出时增减），
        # 不能用 step_index——后者在循环里是 iteration*100+i 之类的序号，
        # 会让日志的「嵌套深度」出现 100/102 这类无意义值。
        log_ctx = self._new_log_context(
            action_id,
            params,
            self.context.current_depth,
            log_action_type,
            log_config=substep_cfg,
        )

        try:
            # 内置动作类经 base 注册表查找（各动作模块自注册）；
            # 自定义（复合）动作经引擎层注入的 resolver 回查 DB。
            action_class = get_action_class(action_id)

            # 如果内置操作没找到，尝试从 DB 查找自定义操作
            if not action_class:
                resolver = get_custom_action_resolver()
                if resolver is not None:
                    action_class = await resolver.get_action_class(action_id)
                if action_class:
                    # 对于自定义复合操作，从 DB 加载 steps 到 params
                    if issubclass(action_class, CompositeAction):
                        db_steps = (
                            await resolver.get_steps(action_id)
                            if resolver is not None
                            else None
                        )
                        if db_steps:
                            if not isinstance(params, dict):
                                if hasattr(params, "model_dump"):
                                    params = params.model_dump()
                                else:
                                    params = {}
                            if not params.get("steps"):
                                params["steps"] = db_steps
                            # 校验 steps 中引用的所有 ca_ 操作是否可访问，防止越权执行
                            await action_crud_svr.validate_steps_referenced_actions(
                                params["steps"]
                                if isinstance(params, dict)
                                else params.steps,
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
            # 透传执行元信息给子步骤（含嵌套复合操作）：
            # 必须带上 workflow_id / browser_id / execution_id，否则 loop / if_else 内的
            # mark_resource_result 会因「缺少 workflow_id 执行上下文」直接失败，
            # 资源去重记录永远写不进去（表现为每轮都把已参与的抽奖重新跑一遍）。
            # 工作流运行里 execution_id 就是运行记录 run_id，只保留 execution_id 一个名字。
            action.exec_meta = {
                "execution_id": self.context.execution_id,
                "parent_execution_id": getattr(
                    self.context, "parent_execution_id", None
                ),
                "browser_id": self.context.browser_id,
                "session_id": self.context.session_id,
                "workflow_id": self.context.workflow_id,
                "log_config": substep_cfg,
            }

            # 执行（使用 execute() 而非 _execute()，确保 _merge_output_vars 被调用）
            result = await action.execute()
            result.replaced_params = (
                params
                if isinstance(params, dict)
                else getattr(params, "model_dump", lambda: {})()
            )

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
                error=f"步骤执行失败({action_id}): {e}",
                action_id=action_id,
                action_name=action_id,
                replaced_params=params
                if isinstance(params, dict)
                else getattr(params, "model_dump", lambda: {})() or {},
            )
            await save_action_log(log_ctx, error_result)
            return error_result

    def _replace_templates(self, params: Dict) -> Dict:
        """模板变量替换（使用递归下降解析）"""
        # 若 params 是 Pydantic 模型，先转为 dict
        if not isinstance(params, dict):
            if hasattr(params, "model_dump"):
                params = params.model_dump()
            else:
                return params

        def replace_value(value):
            if isinstance(value, str):
                # 使用正则匹配 {{变量名}} 格式
                return re.sub(
                    r"\{\{([\w.]+?)\}\}",
                    lambda m: self._get_variable_value(m.group(1)),
                    value,
                )
            elif isinstance(value, dict):
                return {k: replace_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [replace_value(item) for item in value]
            return value

        return replace_value(params)

    def _get_variable_value(self, path: str) -> str:
        """获取变量值（支持点路径）。

        变量缺失或值为 None 时**显式抛错**，而不是把 `{{path}}` 字面量原样带入
        下游步骤（URL、输入框等），避免静默产出错误内容。
        """
        parts = path.split(".")
        placeholder = "{{" + path + "}}"
        current: Any = self.context.variables
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                raise ValueError(f"模板变量未解析: {placeholder}（缺少 '{part}'）")
            current = current[part]
        if current is None:
            raise ValueError(f"模板变量值为空: {placeholder}")
        return str(current)

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
        if not isinstance(params, dict) and hasattr(params, "model_dump"):
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


def _cfg_get(obj: Any, key: str, default: Any = None) -> Any:
    """从 dict 或 Pydantic 模型安全读取字段。

    复合动作内部的步骤经过 discriminated union 校验后，``step.params`` 是
    Pydantic 模型实例；而工作流引擎直接执行时可能是 dict。二者都要兼容，
    因此在控制流策略里统一用本函数取值（模型无 ``.get``，直接调用会抛
    ``AttributeError``）。
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_children(children: Any) -> List[WorkflowStep]:
    """把分支/循环体的子步骤统一成 WorkflowStep 模型实例。

    复合动作的 steps 从 DB（JSON）读回时，``loopBranch`` / ``TrueBranch`` /
    ``FalseBranch`` 里的元素是**原始 dict**（外层 discriminated union 只校验
    顶层步骤，嵌套步骤不会被转换成模型）。而 StepExecutor 依赖 ``step.action_id``
    等属性访问，直接传 dict 会抛 ``'dict' object has no attribute 'action_id'``。
    这里对 dict 逐个套用与 CompositeAction 相同的反序列化逻辑补齐模型。
    同时递归补齐其嵌套分支，保证深层嵌套循环/分支也可执行。
    """
    normalized: List[WorkflowStep] = []
    for child in children or []:
        if isinstance(child, BaseWorkflowStep):
            normalized.append(child)
        elif isinstance(child, dict):
            step = workflow_step_adapter.validate_python(_ensure_action_type(child))
            normalized.append(step)
            _normalize_nested_branches(step)
    return normalized


#: 控制流步骤承载子步骤的参数名（与 PipelineBuilder / StepExecutor 读取的一致）
_BRANCH_PARAM_KEYS: tuple[str, ...] = ("loopBranch", "TrueBranch", "FalseBranch")


def _normalize_nested_branches(step: BaseWorkflowStep) -> None:
    """递归把步骤内部的嵌套分支也规范化成模型。

    注意这里必须同时兼容两种 params 形态：反序列化后 ``step.params`` 是 pydantic
    模型（LoopParams / IfElseParams），只有「未经 union 校验的原始 dict」才是 dict。
    旧实现只在 ``cfg`` 是 dict 时递归，导致「循环 → 分支 → 循环」这类三层嵌套里
    最内层仍是原始 dict，执行时 ``self.params.loopBranch`` 直接失效
    （表现为内层循环整体不执行且无日志）。
    """
    cfg = step.params
    if cfg is None:
        return

    if isinstance(cfg, dict):
        for key in _BRANCH_PARAM_KEYS:
            value = cfg.get(key)
            if isinstance(value, list) and value:
                cfg[key] = _normalize_children(value)
                for child in cfg[key]:
                    _normalize_nested_branches(child)
        return

    updates: dict[str, list[WorkflowStep]] = {}
    for key in _BRANCH_PARAM_KEYS:
        value = getattr(cfg, key, None)
        if isinstance(value, list) and value:
            updates[key] = _normalize_children(value)
    if not updates:
        return
    try:
        step.params = cfg.model_copy(update=updates)
    except Exception:  # noqa: BLE001 - 模型不允许赋值时退回逐个字段设置
        for key, value in updates.items():
            setattr(cfg, key, value)
        step.params = cfg

    for children in updates.values():
        for child in children:
            _normalize_nested_branches(child)


def _collect_logs(results: List[ActionResult], offset: int = 0) -> List[str]:
    """汇总子步骤的过程日志。

    复合动作 / 控制流自身不产生日志，用户可见的过程日志都来自子步骤（原子操作）。
    这里把每个子步骤 ``result.logs`` 打上「步骤序号 + 操作名」前缀后按执行顺序合并，
    让父操作的一条日志记录即可还原整条执行链路。循环 / 分支也返回汇总后的 logs，
    因此嵌套控制流的日志可逐层向上冒泡。
    """
    merged: List[str] = []
    for i, r in enumerate(results):
        label = str(
            getattr(r, "action_name", "") or getattr(r, "action_id", "") or "step"
        )
        for line in getattr(r, "logs", None) or []:
            merged.append(f"[步骤{offset + i + 1} {label}] {line}")
    return merged


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
        self,
        params: LoopParams | Dict,
        loop_item_var: str,
        loop_index_var: str,
    ) -> dict[str, Any]:
        """解析参数映射，将循环项字段映射为目标参数值"""
        mapping = getattr(params, "param_mapping", None) or (
            params.get("param_mapping") if isinstance(params, dict) else None
        )  # type: ignore[union-attr]
        if not mapping:
            return {}

        resolved: dict[str, Any] = {}
        for target_key, source_path in mapping.items():
            # 解析源路径：支持 loop_item.field 和 loop_index
            source_path_str = str(source_path)
            if source_path_str.startswith(f"{loop_item_var}."):
                field_path = source_path_str[len(loop_item_var) + 1 :]
                item = self.context.variables.get(loop_item_var)
                resolved[target_key] = self._extract_field(item, field_path)
            elif source_path_str == loop_item_var:
                resolved[target_key] = self.context.variables.get(loop_item_var)
            elif source_path_str == loop_index_var:
                resolved[target_key] = self.context.variables.get(loop_index_var)
            else:
                # 直接按路径从 variables 中解析
                resolved[target_key] = self._extract_field(
                    self.context.variables, source_path_str
                )

        return resolved

    def _apply_mapped_params(
        self, step: WorkflowStep, mapped: dict[str, Any]
    ) -> WorkflowStep:
        """将映射后的参数注入到步骤参数中（浅拷贝步骤）"""
        if not mapped:
            return step

        # 获取现有 params
        raw_params = step.params or {}
        if not isinstance(raw_params, dict):
            if hasattr(raw_params, "model_dump"):
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
        # 统一把 dict 子步骤（DB JSON 读回）规范化为 WorkflowStep 模型
        children = _normalize_children(
            _cfg_get(params, "loopBranch") or step.children or []
        )

        if not children:
            return ActionResult(
                success=False,
                error="循环体为空",
                action_id=BuiltinActionType.LOOP,
                action_name=BuiltinActionName.LOOP,
            )

        # 获取变量名配置
        loop_item_var = _cfg_get(params, "loop_item_var") or "loop_item"
        loop_index_var = _cfg_get(params, "loop_index_var") or "loop_index"

        # 获取 break/continue 条件（支持 dict 或 ConditionRule）
        break_cond = _cfg_get(params, "break_condition")
        continue_cond = _cfg_get(params, "continue_condition")
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
                action_name=BuiltinActionName.LOOP,
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
                mapped_params = self._resolve_param_mapping(
                    params, loop_item_var, loop_index_var
                )

                # 对子步骤应用参数映射
                mapped_children = [
                    self._apply_mapped_params(child, mapped_params)
                    if isinstance(child, dict) or hasattr(child, "params")
                    else child
                    for child in children
                ]

                # 执行子步骤（带 break/continue 条件判断）
                (
                    child_results,
                    should_break,
                    should_continue,
                ) = await self._execute_children(
                    mapped_children,
                    iteration,
                    break_condition=break_cond,
                    continue_condition=continue_cond,
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

        # 失败传播：任一步骤失败（含重试耗尽）→ 循环失败。
        # 旧实现恒返回 success=True，会把「迭代内步骤失败后中断」掩盖掉，
        # 导致复合动作整体谎报成功、错误无法定位。
        failed = next((r for r in results if not r.success), None)
        return ActionResult(
            success=failed is None,
            error=failed.error if failed else None,
            data=LoopResult(
                iterations=iteration,
                total_results=len(results),
                results=[
                    {"action_id": r.action_id, "success": r.success, "error": r.error}
                    for r in results
                ],
                was_broken=was_broken,
                was_continued=was_continued,
            ),
            execution_time=sum(r.execution_time for r in results),
            action_id=BuiltinActionType.LOOP,
            action_name=BuiltinActionName.LOOP,
            logs=_collect_logs(results),
        )

    def _create_loop_iterator(self, params: Dict) -> "LoopIterator":
        """创建循环迭代器"""
        # 新版 loop_source 模式
        loop_source = _cfg_get(params, "loop_source", "fixed_count")
        loop_items_var = _cfg_get(params, "loop_items_var")
        loop_items_expr = _cfg_get(params, "loop_items_expr")
        loop_items_json = _cfg_get(params, "loop_items_json")

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
            logger.warning(
                f"loop_items_var '{loop_items_var}' 解析结果不是列表: {type(items)}"
            )
            return ListIterator([])

        if loop_source == "expression" and loop_items_expr:
            # 安全评估表达式获取 items
            try:
                tree = ast.parse(loop_items_expr.strip(), mode="eval")
                items = _eval_ast_node(tree.body, self.context.variables)
                if isinstance(items, list):
                    return ListIterator(items)
                logger.warning(f"loop_items_expr 解析结果不是列表: {type(items)}")
                return ListIterator([])
            except Exception as e:
                logger.warning(f"loop_items_expr 评估失败: {e}")
                return ListIterator([])

        if loop_source == "fixed_count":
            count = _cfg_get(params, "count", 1)
            return CountIterator(count)

        # 向后兼容旧字段
        items = _cfg_get(params, "items")
        loop_count = _cfg_get(params, "loop_count")
        loop_while = _cfg_get(params, "loop_while")
        loop_until = _cfg_get(params, "loop_until")

        if items:
            return ListIterator(items)
        elif loop_count:
            return CountIterator(loop_count)
        elif loop_while or loop_until:
            return ConditionIterator(loop_while, loop_until, self.context)
        else:
            return CountIterator(1)

    async def _execute_children(
        self,
        children: List[WorkflowStep],
        iteration: int,
        break_condition: str | None = None,
        continue_condition: str | None = None,
    ) -> tuple[List[ActionResult], bool, bool]:
        """执行子步骤，返回 (results, should_break, should_continue)

        子步骤按 ``step.retry`` 重试（旧实现直接 ``executor.execute()``，
        步骤 JSON 上声明的 retry 被静默忽略：既不重试，失败后还继续往下跑）；
        任一步骤失败（含重试耗尽）即中断后续子步骤，由 LoopStrategy 中断
        整个循环并向上传播失败。
        """
        results = []
        for i, child_step in enumerate(children):
            attempt_results = await self.executor.execute_with_retry(
                child_step, iteration * 100 + i
            )
            result = attempt_results[-1]
            results.append(result)

            # 每步执行后检查 break/continue 条件
            if break_condition and self._evaluate_condition(break_condition):
                return results, True, False
            if continue_condition and self._evaluate_condition(continue_condition):
                return results, False, True

            # 任一步骤失败（含重试耗尽）即中断后续子步骤；
            # 但 on_error=continue 显式要求「失败也继续往下走」——与工作流执行
            # 链路（pipeline.py）语义对齐：被容忍的失败不再中断后续步骤
            if not result.success:
                if child_step.on_error == OnErrorEnum.CONTINUE:
                    result.tolerated = True
                    continue
                break
        return results, False, False


class IfElseStrategy(ExecutionStrategy):
    """条件分支执行策略"""

    async def execute(self, step: WorkflowStep, step_index: int = 0) -> ActionResult:
        """执行条件分支"""
        params = step.params or {}

        # 获取条件（兼容模型 / dict）
        raw_condition = (
            params.condition
            if hasattr(params, "condition")
            else params.get("condition")
        )
        # 兼容 dict 形式（JSON 反序列化时 params 可能是 dict）
        if isinstance(raw_condition, dict):
            condition = ConditionRule.model_validate(raw_condition)
        else:
            condition = raw_condition
        true_branch = _normalize_children(
            list(params.TrueBranch)
            if hasattr(params, "TrueBranch") and params.TrueBranch
            else []
        )
        false_branch = _normalize_children(
            list(params.FalseBranch)
            if hasattr(params, "FalseBranch") and params.FalseBranch
            else []
        )

        # 递归深度检查
        if self.context.current_depth >= settings.workflow_max_nesting_depth:
            return ActionResult(
                success=False,
                error=f"嵌套深度超过限制 ({self.context.current_depth}/{settings.workflow_max_nesting_depth})",
                action_id=BuiltinActionType.IF_ELSE,
                action_name=BuiltinActionName.IF_ELSE,
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
                action_name=BuiltinActionName.IF_ELSE,
            )

        # 保存状态并执行
        saved_state = self.context.save_state()
        self.context.current_depth += 1

        try:
            results = await self._execute_branch(selected_branch)
            failed = next((r for r in results if not r.success), None)

            return ActionResult(
                success=failed is None,
                error=failed.error if failed else None,
                data=IfElseResult(
                    branch=branch_name,
                    executed=True,
                    results=[
                        {"action_id": r.action_id, "success": r.success, "error": r.error}
                        for r in results
                    ],
                ),
                execution_time=sum(r.execution_time for r in results),
                action_id=BuiltinActionType.IF_ELSE,
                action_name=BuiltinActionName.IF_ELSE,
                logs=_collect_logs(results),
            )
        finally:
            self.context.current_depth -= 1
            self.context.restore_state(saved_state)

    async def _execute_branch(self, branch: List[WorkflowStep]) -> List[ActionResult]:
        """执行分支步骤

        按 ``step.retry`` 重试（旧实现直接 ``executor.execute()``，retry 被
        静默忽略）；任一步骤失败（含重试耗尽）即中断后续步骤，避免在错误
        页面上继续点击 / 输入 / 回写结果。
        """
        results = []
        for i, step in enumerate(branch):
            attempt_results = await self.executor.execute_with_retry(step, i)
            results.extend(attempt_results)
            if not attempt_results[-1].success:
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


class LoopAction(BaseAction[LoopParams]):
    """循环操作"""

    action_id: BuiltinActionType = BuiltinActionType.LOOP
    action_type: BuiltinActionType = BuiltinActionType.LOOP
    params: LoopParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: LoopParams | None = None,
        timeout: int = 30000,
        input_vars: dict | None = None,
        output_vars: List[str] | None = None,
        action_name: str | None = None,
    ):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            "action_id": cls.action_id,
            "action_type": cls.action_type,
            "mid": mid,
            "page": page,
            "params": safe_params,
            "timeout": timeout,
            "input_vars": input_vars or {},
            "output_vars": output_vars or [],
            "variables": variables or {},
        }
        if action_name is not None:
            kwargs["_action_name"] = action_name
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

        # 将 data 转为 dict（支持 dict 和模型实例）
        if isinstance(data, dict):
            data_dict = data
        elif hasattr(data, "model_dump"):
            data_dict = data.model_dump()
        else:
            return

        # 如果有 output_vars，按顺序把 data 的值赋给 output_vars 中对应的变量名
        if self.output_vars:
            data_values = list(data_dict.values())
            for i, var_name in enumerate(self.output_vars):
                if i < len(data_values):
                    self.variables[var_name] = data_values[i]

    async def _execute(self) -> ActionResult[LoopResult]:
        """执行循环 - 支持 loop_source (fixed_count/variable/expression) + param_mapping"""
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or not validated_params:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.action_id,
                action_name=self.action_name,
            )

        # 获取子步骤（从 params 中获取）
        children = self.params.loopBranch
        if not children:
            return ActionResult(
                success=True,
                data=LoopResult(message="无子步骤可执行"),
                action_id=self.action_id,
                action_name=self.action_name,
            )

        # 获取变量名配置
        loop_item_var = (
            getattr(self.params, "loop_item_var", "loop_item") or "loop_item"
        )
        loop_index_var = (
            getattr(self.params, "loop_index_var", "loop_index") or "loop_index"
        )

        # 创建执行上下文和执行器
        context = ExecutionContext(self.variables, self.exec_meta)
        executor = StepExecutor(context, self.page, self.mid)

        # 解析 param_mapping
        param_mapping: dict[str, str] = (
            getattr(self.params, "param_mapping", None) or {}
        )

        # 获取循环参数
        loop_source = (
            getattr(self.params, "loop_source", "fixed_count") or "fixed_count"
        )
        count = getattr(self.params, "count", 1) or 1
        loop_items_var = getattr(self.params, "loop_items_var", None)
        loop_items_expr = getattr(self.params, "loop_items_expr", None)
        loop_items_json = getattr(self.params, "loop_items_json", None)

        # 获取 break/continue 条件（支持 dict 或 ConditionRule）
        break_cond_raw = getattr(self.params, "break_condition", None)
        continue_cond_raw = getattr(self.params, "continue_condition", None)
        # dict 反序列化为 ConditionRule（JSON 从前端传来时是 dict）
        if isinstance(break_cond_raw, dict):
            break_cond_raw = ConditionRule.model_validate(break_cond_raw)
        if isinstance(continue_cond_raw, dict):
            continue_cond_raw = ConditionRule.model_validate(continue_cond_raw)

        # 向后兼容旧参数
        loop_count = getattr(self.params, "loop_count", None)
        loop_while = getattr(self.params, "loop_while", None)
        loop_until_val = getattr(self.params, "loop_until", None)

        results: list[dict] = []

        # ---------- 迭代源归一化 ----------
        # json_list / variable / expression 三种来源结构完全同构，先归一为 items 列表；
        # 固定次数（旧 loop_count / 新 count）归一是 count；while / until 条件循环保留
        # 各自的轮末求值语义，不并入 items 骨架。
        resolved_items: list[Any] | None = None
        if loop_source == "json_list" and loop_items_json:
            # 直接使用传入的 JSON 列表
            resolved_items = loop_items_json if isinstance(loop_items_json, list) else []
        elif loop_source == "variable" and loop_items_var:
            # 从变量获取 items 列表
            resolved_items = self._resolve_items_from_variable(context, loop_items_var)
        elif loop_source == "expression" and loop_items_expr:
            # 表达式计算 items
            resolved_items = self._resolve_items_from_expr(context, loop_items_expr)

        # 旧字段优先：loop_count（已废弃）非空时覆盖新字段 count
        effective_count = loop_count if (loop_count is not None and loop_count > 0) else count

        if resolved_items is not None:
            # ---------- 列表迭代（json_list / variable / expression） ----------
            for i, item_value in enumerate(resolved_items):
                context.variables[loop_index_var] = i
                context.variables[loop_item_var] = item_value
                # 每次迭代开始前评估 break 条件
                if break_cond_raw and evaluate_rule(break_cond_raw, context.variables):
                    break
                # 每次迭代开始前评估 continue 条件
                if continue_cond_raw and evaluate_rule(
                    continue_cond_raw, context.variables
                ):
                    continue
                mapped = self._resolve_param_mapping_static(
                    param_mapping,
                    item_value,
                    context.variables,
                    loop_item_var,
                    loop_index_var,
                )
                mapped_children = self._inject_params_to_children(children, mapped)
                child_results = await self._execute_steps_with_context(
                    executor, mapped_children
                )
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break

        elif effective_count > 0:
            # ---------- 固定次数循环（旧 loop_count / 新 count 同构） ----------
            for i in range(effective_count):
                context.variables[loop_index_var] = i
                context.variables[loop_item_var] = None
                # 每次迭代开始前评估 break 条件
                if break_cond_raw and evaluate_rule(break_cond_raw, context.variables):
                    break
                # 每次迭代开始前评估 continue 条件
                if continue_cond_raw and evaluate_rule(
                    continue_cond_raw, context.variables
                ):
                    continue
                mapped = self._resolve_param_mapping_static(
                    param_mapping,
                    None,
                    context.variables,
                    loop_item_var,
                    loop_index_var,
                )
                mapped_children = self._inject_params_to_children(children, mapped)
                child_results = await self._execute_children_with_condition(
                    executor,
                    mapped_children,
                    context,
                    loop_while=loop_while,
                    loop_until=loop_until_val,
                )
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break

        elif loop_while:
            # while 条件循环（轮末重新求值；不参与 param_mapping 与 break/continue 门控）
            loop_i = 0
            while safe_evaluate_condition(loop_while, context.variables):
                context.variables[loop_index_var] = loop_i
                context.variables[loop_item_var] = None
                child_results = await self._execute_children_with_condition(
                    executor,
                    children,
                    context,
                    loop_while=loop_while,
                    loop_until=loop_until_val,
                )
                results.extend(child_results)
                if child_results and not child_results[-1].get("success"):
                    break
                if not safe_evaluate_condition(loop_while, context.variables):
                    break
                loop_i += 1

        elif loop_until_val:
            # until 条件循环（轮末求值退出）
            loop_i = 0
            while True:
                context.variables[loop_index_var] = loop_i
                context.variables[loop_item_var] = None
                child_results = await self._execute_children_with_condition(
                    executor,
                    children,
                    context,
                    loop_while=loop_while,
                    loop_until=loop_until_val,
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
        self.variables["last_output"] = last_output

        # 失败传播：任一步骤失败（含重试耗尽）→ 循环失败。
        # 旧实现恒返回 success=True，会把「迭代内步骤失败后中断」掩盖掉，
        # 导致复合动作整体谎报成功、错误无法定位。
        failed_error: str | None = None
        for entry in results:
            if entry.get("success"):
                continue
            failed_error = next(
                (
                    r.error
                    for r in (entry.get("results") or [])
                    if not r.success and r.error
                ),
                None,
            ) or f"循环内第 {entry.get('iteration')} 步执行失败（无错误详情）"
            break

        return ActionResult(
            success=failed_error is None,
            error=failed_error,
            data=LoopResult(iterations=len(results), details=results),
            execution_time=time.time() - start_time,
            action_id=self.action_id,
            action_name=self.action_name,
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
                field_path = source_path_str[len(loop_item_var) + 1 :]
                resolved[target_key] = LoopAction._extract_field(item_value, field_path)
            elif source_path_str == loop_item_var:
                resolved[target_key] = item_value
            elif source_path_str == loop_index_var:
                resolved[target_key] = variables.get(loop_index_var)
            else:
                resolved[target_key] = LoopAction._extract_field(
                    variables, source_path_str
                )
        return resolved

    @staticmethod
    def _inject_params_to_children(
        children: list[WorkflowStep],
        mapped: dict[str, Any],
    ) -> list[WorkflowStep]:
        """把 param_mapping 的映射值注入子步骤的**同名参数**

        与 pipeline 的同名实现保持一致，两条约束（引擎不得擅自改写用户写死
        的配置）：

        1. **只注入用户声明过该参数名的步骤**：``param_mapping`` 的语义是
           「把某个参数的值换成循环项的字段」，不是给所有步骤凭空加参数。
           旧实现给**每个**子步骤的 params 都加上映射键，等于用循环项的值
           顶掉所有步骤的同名参数（连用户写死的 ``selector`` 也会被覆盖）。
        2. **不改原步骤对象**：返回副本，避免映射值残留在步骤定义里
           （下一轮映射为空时，上一轮的值会继续生效）。
        """
        if not mapped or not children:
            return children
        new_children: list[WorkflowStep] = []
        for child in children:
            raw = child.params or {}
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            elif not isinstance(raw, dict):
                raw = {}
            injected = {k: v for k, v in mapped.items() if k in raw}
            if not injected:
                new_children.append(child)
                continue
            new_children.append(
                child.model_copy(update={"params": {**raw, **injected}})
            )
        return new_children

    def _resolve_items_from_variable(
        self, context: ExecutionContext, var_ref: str
    ) -> list:
        """从变量中解析 items 列表"""
        items = self._extract_field(context.variables, var_ref)
        if isinstance(items, list):
            return items
        logger.warning(f"loop_items_var '{var_ref}' 解析结果不是列表: {type(items)}")
        return []

    def _resolve_items_from_expr(self, context: ExecutionContext, expr: str) -> list:
        """从表达式安全解析 items 列表"""
        try:
            tree = ast.parse(expr.strip(), mode="eval")
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
        """执行循环体内的所有步骤，每步执行前后检查 loop_while/loop_until 条件

        步骤按 ``step.retry`` 重试；任一步骤失败（含重试耗尽）即中断本次
        迭代剩余步骤，由调用方中断整个循环并向上传播失败（与顶层责任链
        语义一致）。旧实现在步骤失败后仍继续执行后续步骤，导致在错误页面
        上继续点击 / 输入，最终谎报成功。
        """
        results = []
        for i, step in enumerate(steps):
            # 每步执行前检查条件
            if loop_while and not safe_evaluate_condition(
                loop_while, context.variables
            ):
                break
            if loop_until and safe_evaluate_condition(loop_until, context.variables):
                break

            attempt_results = await executor.execute_with_retry(step, i)
            results.append(
                {
                    "iteration": i,
                    "success": attempt_results[-1].success,
                    "results": attempt_results,
                }
            )
            # 步骤失败（含重试耗尽）→ 中断本次迭代剩余步骤
            if not attempt_results[-1].success:
                break

            # 每步执行后检查条件
            if loop_until and safe_evaluate_condition(loop_until, context.variables):
                break
        return results

    async def _execute_steps_with_context(
        self, executor: StepExecutor, steps: list[WorkflowStep]
    ) -> list[dict]:
        """使用上下文执行步骤列表（含重试；任一步失败即中断）"""
        results = []
        for i, step in enumerate(steps):
            attempt_results = await executor.execute_with_retry(step, i)
            results.append(
                {
                    "iteration": i,
                    "success": attempt_results[-1].success,
                    "results": attempt_results,
                }
            )
            if not attempt_results[-1].success:
                break
        return results


class IfElseAction(BaseAction[IfElseParams]):
    """条件分支控制流操作"""

    action_id: BuiltinActionType = BuiltinActionType.IF_ELSE
    action_type: BuiltinActionType = BuiltinActionType.IF_ELSE
    params: IfElseParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: IfElseParams | None = None,
        timeout: int = 30000,
        input_vars: dict | None = None,
        output_vars: List[str] | None = None,
        action_name: str | None = None,
    ):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            "action_id": cls.action_id,
            "action_type": cls.action_type,
            "mid": mid,
            "page": page,
            "params": safe_params,
            "timeout": timeout,
            "input_vars": input_vars or {},
            "output_vars": output_vars or [],
            "variables": variables or {},
        }
        if action_name is not None:
            kwargs["_action_name"] = action_name
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
        elif hasattr(data, "model_dump"):
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
            self.params
        )
        if not valid:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.action_id,
                action_name=self.action_name,
            )

        # 获取分支步骤（从 params 中获取）
        true_branch = list(self.params.TrueBranch) if self.params.TrueBranch else []
        false_branch = list(self.params.FalseBranch) if self.params.FalseBranch else []

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
                action_id=self.action_id,
                action_name=self.action_name,
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
        self.variables["last_output"] = last_output

        # 失败传播：分支内任一步骤失败（含重试耗尽）→ 整个条件判断失败。
        # 旧实现恒返回 success=True，嵌套在复合动作中时分支内失败被完全掩盖，
        # 出现「点击转发失败 → 仍继续输入/发布 → 最终谎报成功」的假阳性。
        # 被 on_error=continue 显式容忍的失败不参与判定（与 pipeline 对齐）：
        # 「LLM 失败 → 走抄评论兜底」不应让整个分支判失败。
        failed = next(
            (
                r
                for r in results
                if not r.get("success") and not r.get("tolerated")
            ),
            None,
        )
        return ActionResult(
            success=failed is None,
            error=failed.get("error") if failed else None,
            data=IfElseResult(branch_taken=branch_key, results=results),
            execution_time=time.time() - start_time,
            action_id=self.action_id,
            action_name=self.action_name,
        )

    def _evaluate_condition(self, condition: ConditionRule) -> bool:
        """评估条件规则（纯 Python 逻辑，不使用 eval）"""
        try:
            return evaluate_rule(condition, self.variables)
        except ConditionEvaluateError as e:
            logger.warning(f"IfElseAction 条件评估失败: {e}")
            return False

    async def _execute_steps_with_context(
        self, executor: StepExecutor, steps: List[WorkflowStep]
    ) -> List[dict]:
        """使用上下文执行步骤列表（含重试；任一步失败即中断后续步骤）

        旧实现既不重试也不中断：分支内某步失败后继续执行后续步骤，
        最终由 IfElseAction 恒返回 success=True 掩盖，出现
        「点击转发失败 → 仍继续输入/发布 → 最终谎报成功」的假阳性。
        """
        results = []
        for step in steps:
            attempt_results = await executor.execute_with_retry(step)
            last = attempt_results[-1]
            results.append(
                {
                    "action_id": last.action_id,
                    "action_name": last.action_name,
                    "success": last.success,
                    "error": last.error,
                    "data": last.data,
                    # 被 on_error=continue 容忍的失败不参与分支失败判定
                    "tolerated": bool(last.tolerated),
                }
            )
            if not last.success:
                # on_error=continue 显式要求「失败也继续往下走」（与 pipeline 对齐）：
                # 例如「LLM 生成失败 → 继续走后面的抄评论兜底分支」
                if step.on_error == OnErrorEnum.CONTINUE:
                    last.tolerated = True
                    results[-1]["tolerated"] = True
                    continue
                break
        return results


class ParallelAction(BaseAction[ParallelParams]):
    """多分支并行执行：每个分支可绑定独立页面，分支之间用 asyncio 并发。"""

    action_id: BuiltinActionType = BuiltinActionType.PARALLEL
    action_type: BuiltinActionType = BuiltinActionType.PARALLEL
    params: ParallelParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: ParallelParams | None = None,
        timeout: int = 30000,
        input_vars: Dict | None = None,
        output_vars: List[str] | None = None,
        action_name: str | None = None,
    ):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            "action_id": cls.action_id,
            "action_type": cls.action_type,
            "mid": mid,
            "page": page,
            "params": safe_params,
            "timeout": timeout,
            "input_vars": input_vars or {},
            "output_vars": output_vars or [],
            "variables": variables or {},
        }
        if action_name is not None:
            kwargs["_action_name"] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult:
        start_time = time.time()
        valid, error_msg, validated = self.validate_params_with_model(self.params)
        if not valid or validated is None:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        branches = list(validated.branches or [])
        if not branches:
            return ActionResult(
                success=False,
                error="并行分支为空",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        key = (int(self.mid), int(self.exec_meta.get("browser_id") or 0))

        async def run_branch(branch) -> Dict[str, Any]:
            page = self.page
            if branch.page:
                candidate = page_registry.get(key, branch.page)
                if candidate is None:
                    return {
                        "name": branch.name,
                        "success": False,
                        "error": f"页面 {branch.page!r} 不存在或已关闭",
                        "results": [],
                    }
                page = candidate

            # 分支变量隔离：以父作用域快照为起点，结束时按 export 显式合并
            context = ExecutionContext(dict(self.variables), self.exec_meta)
            context.current_depth = int(self.exec_meta.get("depth") or 0) + 1
            executor = StepExecutor(context, page, self.mid)

            results: List[Dict[str, Any]] = []
            for index, step in enumerate(_normalize_children(branch.steps)):
                attempts = await executor.execute_with_retry(step, index)
                last = attempts[-1]
                results.append(
                    {
                        "action_id": last.action_id,
                        "action_name": last.action_name,
                        "success": last.success,
                        "error": last.error,
                        "data": last.data,
                    }
                )
                if not last.success:
                    break

            for var_name in branch.export:
                if var_name in context.variables:
                    self.variables[var_name] = context.variables[var_name]

            failed = next((r for r in results if not r["success"]), None)
            return {
                "name": branch.name,
                "success": failed is None,
                "error": failed.get("error") if failed else None,
                "results": results,
            }

        branch_results: List[Dict[str, Any]] = []
        tasks = [asyncio.create_task(run_branch(b)) for b in branches]
        try:
            if validated.on_branch_error == "abort":
                for completed in asyncio.as_completed(tasks):
                    result = await completed
                    branch_results.append(result)
                    if not result["success"]:
                        for task in tasks:
                            task.cancel()
                        break
                # 等待被取消的分支收尾，避免 "Task was destroyed" 告警
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                for item in gathered:
                    if isinstance(item, BaseException):
                        branch_results.append(
                            {
                                "name": "?",
                                "success": False,
                                "error": str(item),
                                "results": [],
                            }
                        )
                    else:
                        branch_results.append(item)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        failed_branches = [r for r in branch_results if not r["success"]]
        error = None
        if failed_branches:
            error = "; ".join(
                f"分支 {r['name']} 失败: {r.get('error')}" for r in failed_branches
            )

        return ActionResult(
            success=not failed_branches,
            error=error,
            data={
                "branches": branch_results,
                "failed_branches": [r["name"] for r in failed_branches],
            },
            execution_time=time.time() - start_time,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )

# 自注册进内置动作注册表（base 的依赖倒置槽位，供控制流策略层按 action_id 查找）

register_action_class(ParallelAction)
register_action_class(LoopAction)
register_action_class(IfElseAction)


class CompositeAction(BaseAction[CompositeParams]):
    """组合动作基类 - 使用责任链模式执行步骤"""

    action_id: BuiltinActionType = BuiltinActionType.COMPOSITE
    action_type: BuiltinActionType = BuiltinActionType.COMPOSITE
    params: CompositeParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: CompositeParams | None = None,
        timeout: int = 30000,
        input_vars: dict | None = None,
        output_vars: List[str] | None = None,
        action_name: str | None = None,
    ):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            "action_id": cls.action_id,
            "action_type": cls.action_type,
            "mid": mid,
            "page": page,
            "params": safe_params,
            "timeout": timeout,
            "input_vars": input_vars or {},
            "output_vars": output_vars or [],
            "variables": variables or {},
        }
        if action_name is not None:
            kwargs["_action_name"] = action_name
        return cls(**kwargs)

    @property
    def steps(self):
        if self.params and hasattr(self.params, "steps"):
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
        elif hasattr(data, "model_dump"):
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
            if hasattr(self.params, "steps"):
                raw_steps = self.params.steps
            elif isinstance(self.params, dict):
                raw_steps = self.params.get("steps")
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
                    steps.append(
                        workflow_step_adapter.validate_python(_ensure_action_type(s))
                    )
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
        self.variables["last_output"] = last_output

        total = len(results)
        success_count = sum(1 for r in results if r.success)
        all_success = all(r.success for r in results)

        return ActionResult(
            success=all_success,
            data=CompositeResult(
                total_steps=total,
                success_count=success_count,
                results=[
                    {
                        "action_id": r.action_id,
                        "action_name": r.action_name,
                        "success": r.success,
                        "error": r.error,
                        "execution_time": r.execution_time,
                    }
                    for r in results
                ],
            ),
            error=results[-1].error if results and not all_success else None,
            execution_time=time.time() - start_time,
            action_id=self.action_id,
            action_name=self.action_name,
            logs=_collect_logs(results),
        )

    async def _execute_chain(
        self, executor: StepExecutor, steps: List[WorkflowStep]
    ) -> List[ActionResult]:
        """责任链模式执行步骤列表"""
        results = []

        for i, step in enumerate(steps):
            # 执行步骤（含重试与兜底分支，语义与 execute_with_retry 一致）
            attempt_results = await executor.execute_with_retry(step, i)
            results.extend(attempt_results)
            result = attempt_results[-1]

            # 步骤失败且重试也失败时，中断后续步骤；
            # on_error=continue 显式要求「失败也继续往下走」（与 pipeline 对齐）
            if not result.success:
                if step.on_error == OnErrorEnum.CONTINUE:
                    result.tolerated = True
                    continue
                break

        return results
