"""
Pipeline — 工作流管道

数据结构:
    StepNode = AtomicStep | LoopStep | IfElseStep       (sealed union)
    Pipeline = list[StepNode]

算法:
    Pipeline.execute(scope):  left-fold 遍历步骤序列
        for step in steps:
            if step.condition fails → skip
            resolved_params = scope.resolve_params(step.params)
            result = executor(action_id, resolved_params, scope)
            scope.set_outputs(step.output_vars, result.data)

    LoopStep.execute(scope):  迭代器模式
        scope.push()
        for i in range(count) | while condition:
            scope.set("loop_index", i)
            body.execute(scope)
        scope.pop()

    IfElseStep.execute(scope): 决策树模式
        scope.push()
        branch = true_body if condition(scope) else false_body
        branch.execute(scope)
        scope.pop()
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, List, Protocol

from loguru import logger

from app.models.execution.action_params import BuiltinActionType
from app.models.execution.condition_models import (
    ConditionRule,
    ConditionEvaluateError,
    evaluate_rule,
)
from app.services.execution.actions.base import ActionResult
from app.services.execution.scope import Scope


# ─── Action Executor 协议 ──────────────────────────────

class ActionExecutor(Protocol):
    """执行单个 action 的可调用对象。

    (action_id, resolved_params, scope, output_vars) → ActionResult
    """
    async def __call__(
        self,
        action_id: str,
        params: dict,
        scope: Scope,
        output_vars: list[str],
    ) -> ActionResult: ...


# ─── Step 节点 (sealed union) ──────────────────────────

@dataclass
class StepNode(ABC):
    """步骤节点基类。"""
    action_id: str
    condition: ConditionRule | None = None
    retry: int = 0

    def should_execute(self, scope: Scope) -> bool:
        """条件门控：评估 step.condition。"""
        if self.condition is None:
            return True
        try:
            return evaluate_rule(self.condition, scope.snapshot())
        except ConditionEvaluateError as e:
            logger.warning(f"步骤条件评估失败: {e}")
            return False

    @abstractmethod
    async def execute(
        self,
        scope: Scope,
        executor: ActionExecutor,
    ) -> ActionResult:
        """执行此步骤，返回 ActionResult。子类实现。"""
        ...


@dataclass
class AtomicStep(StepNode):
    """原子步骤 — 执行单个 action。

    字段:
        params:      操作参数（含 {{var}} 模板，执行时由 scope.resolve_params 替换）
        input_vars:  输入变量，执行前合并到 scope（供后续步骤的 {{var}} 引用）
        output_vars: 输出变量名列表，结果按 data.values() 顺序赋值
    """
    params: dict[str, Any] = field(default_factory=dict)
    input_vars: dict[str, Any] = field(default_factory=dict)
    output_vars: list[str] = field(default_factory=list)

    async def execute(self, scope: Scope, executor: ActionExecutor) -> ActionResult:
        # 将 input_vars 合并到当前作用域，使后续模板 {{key}} 可解析到值
        if self.input_vars:
            scope.update(self.input_vars)
        # InputAction 的变量缺失或为 None 时替换为空字符串
        default = "" if self.action_id == BuiltinActionType.INPUT else None
        replaced = scope.resolve_params(self.params, default=default)
        return await executor(
            action_id=self.action_id,
            params=replaced,
            scope=scope,
            output_vars=self.output_vars,
        )


@dataclass
class LoopStep(StepNode):
    """循环步骤 — 重复执行子管道。

    字段:
        body:            Pipeline — 循环体
        count:           固定次数
        loop_condition:  while 条件表达式字符串
        loop_until:      until 条件表达式字符串
        loop_items:      要遍历的 items 列表（variable/expression 模式）
        loop_items_var:  从 scope 变量中获取 items 的引用路径
        loop_item_var:   循环项作用域名（默认 "loop_item"）
        loop_index_var:  循环索引作用域名（默认 "loop_index"）
        param_mapping:   参数映射 {目标参数名: 源字段路径}
    """
    body: Pipeline = field(default_factory=lambda: Pipeline([]))
    count: int | None = None
    loop_condition: str | None = None
    loop_until: str | None = None
    loop_items: list[Any] | None = None
    loop_items_var: str | None = None
    loop_item_var: str = "loop_item"
    loop_index_var: str = "loop_index"
    param_mapping: dict[str, str] | None = None

    def _resolve_loop_items(self, scope: Scope) -> list[Any]:
        """运行时解析 loop_items（从 scope 变量中获取）"""
        if self.loop_items and any(item is not None for item in self.loop_items):
            return self.loop_items  # 已有静态列表
        if self.loop_items_var:
            value = self._extract_field(scope.snapshot(), self.loop_items_var)
            if isinstance(value, list):
                return value
        return []

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

    def _resolve_param_mapping(self, scope: Scope) -> dict[str, Any]:
        """解析参数映射为实际值"""
        if not self.param_mapping:
            return {}
        resolved: dict[str, Any] = {}
        item = scope.get(self.loop_item_var)
        index = scope.get(self.loop_index_var)
        for target_key, source_path in self.param_mapping.items():
            source_str = str(source_path)
            if source_str.startswith(f"{self.loop_item_var}."):
                field_path = source_str[len(self.loop_item_var) + 1:]
                resolved[target_key] = self._extract_field(item, field_path)
            elif source_str == self.loop_item_var:
                resolved[target_key] = item
            elif source_str == self.loop_index_var:
                resolved[target_key] = index
            else:
                resolved[target_key] = self._extract_field(scope.snapshot(), source_str)
        return resolved

    def _inject_mapped_params(self, pipeline: Pipeline, mapped: dict[str, Any]) -> Pipeline:
        """将映射参数注入 Pipeline 中每个 AtomicStep 的 params"""
        if not mapped:
            return pipeline
        for step in pipeline.steps:
            if isinstance(step, AtomicStep):
                step.params = {**step.params, **mapped}
        return pipeline

    async def execute(self, scope: Scope, executor: ActionExecutor) -> ActionResult:
        results: list[ActionResult] = []

        # 运行时解析 loop_items
        resolved_items = self._resolve_loop_items(scope)

        if resolved_items:
            results = await self._loop_by_items(scope, executor, resolved_items)
        elif self.count is not None:
            results = await self._loop_by_count(scope, executor)
        elif self.loop_condition:
            results = await self._loop_by_while(scope, executor)
        elif self.loop_until:
            results = await self._loop_by_until(scope, executor)

        return ActionResult(
            success=True,
            data={"iterations": len(results)},
            action_id=self.action_id,
            action_name="loop",
        )

    async def _loop_by_items(self, scope: Scope, executor: ActionExecutor, items: list[Any]) -> list[ActionResult]:
        """遍历 items 列表执行循环体"""
        results = []
        for i, item_value in enumerate(items):
            scope.set(self.loop_index_var, i)
            scope.set(self.loop_item_var, item_value)
            mapped = self._resolve_param_mapping(scope)
            body = self._inject_mapped_params(self.body, mapped)
            ir = await body.execute(scope, executor)
            results.extend(ir)
            if ir and not ir[-1].success:
                break
        return results

    async def _loop_by_count(self, scope: Scope, executor: ActionExecutor) -> list[ActionResult]:
        results = []
        for i in range(self.count):
            scope.set(self.loop_index_var, i)
            ir = await self.body.execute(scope, executor)
            results.extend(ir)
            if ir and not ir[-1].success:
                break
        return results

    async def _loop_by_while(self, scope: Scope, executor: ActionExecutor) -> list[ActionResult]:
        from app.services.execution.actions.control_flow import safe_evaluate_condition

        results = []
        i = 0
        while safe_evaluate_condition(self.loop_condition, scope.snapshot()):
            scope.set(self.loop_index_var, i)
            ir = await self.body.execute(scope, executor)
            results.extend(ir)
            if ir and not ir[-1].success:
                break
            i += 1
        return results

    async def _loop_by_until(self, scope: Scope, executor: ActionExecutor) -> list[ActionResult]:
        from app.services.execution.actions.control_flow import safe_evaluate_condition

        results = []
        i = 0
        while True:
            scope.set(self.loop_index_var, i)
            ir = await self.body.execute(scope, executor)
            results.extend(ir)
            if ir and not ir[-1].success:
                break
            if safe_evaluate_condition(self.loop_until, scope.snapshot()):
                break
            i += 1
        return results


@dataclass
class IfElseStep(StepNode):
    """条件分支步骤 — 根据条件选择执行 true/false 子管道。

    字段:
        condition_rule: ConditionRule — 分支条件
        true_body:      Pipeline | None
        false_body:     Pipeline | None
    """
    condition_rule: ConditionRule | None = None
    true_body: Pipeline | None = None
    false_body: Pipeline | None = None

    async def execute(self, scope: Scope, executor: ActionExecutor) -> ActionResult:
        try:
            take_true = (
                evaluate_rule(self.condition_rule, scope.snapshot())
                if self.condition_rule is not None
                else False
            )
        except ConditionEvaluateError as e:
            logger.warning(f"if_else 条件评估失败: {e}")
            take_true = False

        selected = self.true_body if take_true else self.false_body
        if selected is None:
            return ActionResult(success=True, action_id=self.action_id, action_name="if_else")

        results = await selected.execute(scope, executor)
        return ActionResult(
            success=True,
            data={"branch": "true" if take_true else "false", "results": results},
            action_id=self.action_id,
            action_name="if_else",
        )


# ─── Pipeline — 步骤序列 ───────────────────────────────

@dataclass
class Pipeline:
    """步骤序列。

    算法：left-fold — 依次执行每个 StepNode，在 Scope 上累积副作用。

        foldl (results, scope) step →
            if step.should_execute(scope):
                result = step.execute(scope, executor)
                (results + [result], scope)

    时间复杂度：O(N)，N 为步骤数。
    空间复杂度：O(N)，存储结果列表。
    """
    steps: list[StepNode]

    async def execute(
        self,
        scope: Scope,
        executor: ActionExecutor,
    ) -> list[ActionResult]:
        results: list[ActionResult] = []

        for step in self.steps:
            try:
                if not step.should_execute(scope):
                    logger.info(f"跳过步骤 {step.action_id}（条件不满足）")
                    continue

                logger.info(f"▶ 执行步骤: {step.action_id}")
                start = time.time()
                result = await step.execute(scope, executor)
                result.execution_time = time.time() - start
                result.action_id = step.action_id
                results.append(result)

                # retry == 0 表示不重试，失败即停止
                if not result.success and step.retry == 0:
                    break

                # retry > 0 时重试
                if not result.success and step.retry > 0:
                    for retry_i in range(step.retry):
                        logger.info(f"重试 {step.action_id} ({retry_i + 1}/{step.retry})")
                        result = await step.execute(scope, executor)
                        if result.success:
                            break
                    results[-1] = result

            except Exception as e:
                logger.error(f"步骤异常: {step.action_id} - {e}")
                results.append(ActionResult(
                    success=False, error=str(e), execution_time=0,
                    action_id=step.action_id, action_name=step.action_id,
                ))
                break

        return results


# ─── Builder — 从 WorkflowStep 构建 StepNode ────────────

class PipelineBuilder:
    """将 WorkflowStep 列表编译为 Pipeline IR。

    编译策略（按 action_type 分发）:
        - LOOP     → LoopStep(body = Pipeline(children))
        - IF_ELSE  → IfElseStep(true_body, false_body)
        - 其他      → AtomicStep
    """

    @staticmethod
    def _get_attr(obj, key: str, default=None):
        """安全获取属性（兼容 dict 和对象）。"""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def build(
        steps: list,
        *,
        plugin_count_hint: int = 0,
    ) -> Pipeline:
        """编译步骤列表。

        Args:
            steps: WorkflowStep 对象列表（或 dict）
        Returns:
            Pipeline 中间表示
        """
        nodes: list[StepNode] = []
        for s in steps:
            action_id = PipelineBuilder._get_attr(s, "action_id", "")
            action_type = PipelineBuilder._get_attr(s, "action_type") or PipelineBuilder._get_attr(s, "action_id", "")
            condition = PipelineBuilder._get_attr(s, "condition")

            # LOOP
            if action_type == BuiltinActionType.LOOP:
                children = PipelineBuilder._get_children(s)
                count = getattr(s, "count", None) or PipelineBuilder._get_param(s, "count")
                loop_while = getattr(s, "loop_while", None) or PipelineBuilder._get_param(s, "loop_while")
                loop_until = getattr(s, "loop_until", None) or PipelineBuilder._get_param(s, "loop_until")
                loop_source = PipelineBuilder._get_param(s, "loop_source") or "fixed_count"
                loop_items_var = PipelineBuilder._get_param(s, "loop_items_var")
                loop_item_var = PipelineBuilder._get_param(s, "loop_item_var") or "loop_item"
                loop_index_var = PipelineBuilder._get_param(s, "loop_index_var") or "loop_index"
                param_mapping = PipelineBuilder._get_param(s, "param_mapping")

                # 确定迭代次数
                if loop_source == "fixed_count":
                    count = count or 1
                else:
                    count = None  # variable/expression 模式不设 count

                # 向后兼容旧字段
                old_loop_count = getattr(s, "loop_count", None) or PipelineBuilder._get_param(s, "loop_count")
                if count is None and old_loop_count is not None:
                    count = old_loop_count

                nodes.append(LoopStep(
                    action_id=action_id,
                    condition=condition,
                    body=PipelineBuilder.build(children) if children else Pipeline([]),
                    count=count,
                    loop_condition=loop_while,
                    loop_until=loop_until,
                    loop_items_var=loop_items_var if loop_source != "fixed_count" else None,
                    loop_item_var=loop_item_var,
                    loop_index_var=loop_index_var,
                    param_mapping=param_mapping,
                ))

            # IF_ELSE
            elif action_type == BuiltinActionType.IF_ELSE:
                true_raw = PipelineBuilder._get_param(s, "TrueBranch")
                false_raw = PipelineBuilder._get_param(s, "FalseBranch")
                rule_raw = PipelineBuilder._get_param(s, "condition")
                rule = ConditionRule.model_validate(rule_raw) if isinstance(rule_raw, dict) else rule_raw

                nodes.append(IfElseStep(
                    action_id=action_id,
                    condition=condition,
                    condition_rule=rule,
                    true_body=PipelineBuilder.build(true_raw) if true_raw else None,
                    false_body=PipelineBuilder.build(false_raw) if false_raw else None,
                ))

            # Atomic / Composite (default)
            else:
                params = PipelineBuilder._get_attr(s, "params", {}) or {}
                input_vars = PipelineBuilder._get_attr(s, "input_vars", None) or {}
                output_vars = PipelineBuilder._get_attr(s, "output_vars", None) or []
                retry = PipelineBuilder._get_attr(s, "retry", 0) or 0
                nodes.append(AtomicStep(
                    action_id=action_id,
                    condition=condition,
                    retry=retry,
                    params=params if isinstance(params, dict) else params.model_dump(),
                    input_vars=input_vars if isinstance(input_vars, dict) else {},
                    output_vars=output_vars,
                ))

        return Pipeline(steps=nodes)

    @staticmethod
    def _get_children(step) -> list:
        """获取步骤的子步骤列表（兼容 children 和 params.steps）。"""
        children = PipelineBuilder._get_attr(step, "children")
        if children:
            return children
        if branch := PipelineBuilder._get_param(step, "loopBranch"):
            if isinstance(branch, list):
                return branch
        if steps := PipelineBuilder._get_param(step, "steps"):
            if isinstance(steps, list):
                return steps
        return []

    @staticmethod
    def _get_param(step, key: str, default=None):
        """安全获取 params 中的字段（兼容 dict 和模型）。"""
        params = getattr(step, "params", {}) or {}
        if isinstance(params, dict):
            return params.get(key, default)
        return getattr(params, key, default)
