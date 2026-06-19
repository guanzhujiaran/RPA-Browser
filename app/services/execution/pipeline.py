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
        replaced = scope.resolve_params(self.params)
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
        body:     Pipeline — 循环体
        count:    固定次数（与 condition 互斥）
        condition: 条件表达式字符串（while/until 逻辑由外部计算）
    """
    body: Pipeline = field(default_factory=lambda: Pipeline([]))
    count: int | None = None
    loop_condition: str | None = None       # while 条件
    loop_until: str | None = None           # until 条件

    async def execute(self, scope: Scope, executor: ActionExecutor) -> ActionResult:
        results: list[ActionResult] = []

        if self.count is not None:
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

    async def _loop_by_count(self, scope: Scope, executor: ActionExecutor) -> list[ActionResult]:
        results = []
        for i in range(self.count):
            scope.set("loop_index", i)
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
            scope.set("loop_index", i)
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
            scope.set("loop_index", i)
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
            action_id = s.action_id if hasattr(s, "action_id") else s.get("action_id", "")
            action_type = getattr(s, "action_type", None) or getattr(s, "action_id", "")
            condition = getattr(s, "condition", None)

            # LOOP
            if action_type == BuiltinActionType.LOOP:
                children = PipelineBuilder._get_children(s)
                count = getattr(s, "loop_count", None)
                loop_while = getattr(s, "loop_while", None) or PipelineBuilder._get_param(s, "loop_while")
                loop_until = getattr(s, "loop_until", None) or PipelineBuilder._get_param(s, "loop_until")
                if count is None:
                    count = PipelineBuilder._get_param(s, "count")

                nodes.append(LoopStep(
                    action_id=action_id,
                    condition=condition,
                    body=PipelineBuilder.build(children) if children else Pipeline([]),
                    count=count,
                    loop_condition=loop_while,
                    loop_until=loop_until,
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
                params = getattr(s, "params", {}) or {}
                input_vars = getattr(s, "input_vars", None) or {}
                output_vars = getattr(s, "output_vars", None) or []
                retry = getattr(s, "retry", 0) or 0
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
        children = getattr(step, "children", None)
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
