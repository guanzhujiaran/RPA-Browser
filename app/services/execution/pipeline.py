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
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, List, Protocol

from loguru import logger

from app.models.execution.action_params import BuiltinActionType, OnErrorEnum
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
    on_error: OnErrorEnum = OnErrorEnum.STOP
    on_error_branch: Pipeline | None = None  # 失败时执行的子管道（回退/清理/告警等）

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
        # 严格解析参数模板：任一变量缺失 / 值为 None / 点路径中断即抛
        # TemplateResolveError，终止本步骤（禁止把未解析的 {{placeholder}}
        # 字面量或空值带入动作参数继续执行）。
        replaced = scope.resolve_params(self.params, strict=True)
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
    # 每轮开始前评估：break=跳出整个循环，continue=跳过本轮循环体
    break_condition: ConditionRule | None = None
    continue_condition: ConditionRule | None = None
    # 每轮结束后的固定间隔（秒，0=不等待）。成功/失败都等，语义类似 finally。
    iteration_wait_seconds: float = 0.0

    def _eval_condition(self, rule: ConditionRule | None, scope: Scope) -> bool:
        """安全评估 break/continue 条件（异常视为不命中，不打断循环）。"""
        if rule is None:
            return False
        try:
            return bool(evaluate_rule(rule, scope.snapshot()))
        except Exception as exc:  # noqa: BLE001 - 条件异常不阻断执行
            logger.warning(f"循环条件评估失败: {exc}")
            return False

    def _should_break(self, scope: Scope) -> bool:
        return self._eval_condition(self.break_condition, scope)

    def _should_continue(self, scope: Scope) -> bool:
        return self._eval_condition(self.continue_condition, scope)

    #: 循环体尾部固定等待的随机区间（秒）：避免固定节奏，降低风控识别
    TAIL_WAIT_MIN_SECONDS = 10
    TAIL_WAIT_MAX_SECONDS = 30

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
                field_path = source_str[len(self.loop_item_var) + 1 :]
                resolved[target_key] = self._extract_field(item, field_path)
            elif source_str == self.loop_item_var:
                resolved[target_key] = item
            elif source_str == self.loop_index_var:
                resolved[target_key] = index
            else:
                resolved[target_key] = self._extract_field(scope.snapshot(), source_str)
        return resolved

    def _inject_mapped_params(
        self, pipeline: Pipeline, mapped: dict[str, Any]
    ) -> Pipeline:
        """将映射参数注入 Pipeline 中每个 AtomicStep 的 params"""
        if not mapped:
            return pipeline
        for step in pipeline.steps:
            if isinstance(step, AtomicStep):
                step.params = {**step.params, **mapped}
        return pipeline

    async def _report_iteration(
        self, scope: Scope, index: int, total: int
    ) -> None:
        """上报循环轮次（每层都发消息；进度维度由上报器挑最外层采纳）。"""
        reporter = scope.progress
        if reporter is None:
            return
        await reporter.loop_iteration(
            action_id=self.action_id,
            name=self.action_id,
            index=index + 1,
            total=total,
            depth=scope.loop_nesting,
            # 循环体动作数：第二条进度条（本轮内动作进度）的分母
            body_total=len(self.body.steps),
        )

    async def execute(self, scope: Scope, executor: ActionExecutor) -> ActionResult:
        results: list[ActionResult] = []
        # 实际执行轮次（continue 跳过的轮次不计）
        rounds = 0

        # 运行时解析 loop_items
        resolved_items = self._resolve_loop_items(scope)

        loop_depth = scope.loop_nesting + 1
        scope.loop_nesting += 1
        try:
            if resolved_items:
                results, rounds = await self._loop_by_items(
                    scope, executor, resolved_items
                )
            elif self.count is not None:
                results, rounds = await self._loop_by_count(scope, executor)
            elif self.loop_condition:
                results, rounds = await self._loop_by_while(scope, executor)
            elif self.loop_until:
                results, rounds = await self._loop_by_until(scope, executor)
        finally:
            scope.loop_nesting -= 1
            # 每层循环结束都上报（打印是全量的）；上报器内部只在「被跟踪的
            # 最外层」结束时清空循环维度进度，避免界面停在最后一轮的 N/N。
            if scope.progress is not None:
                await scope.progress.loop_finished(self.action_id, depth=loop_depth)

        # 失败传播：任一**未被容忍**的迭代失败（即使策略是 continue 跳过了后续动作）都要
        # 向上报告，否则工作流会「假成功」——界面显示全部步骤成功，实际却跳过了数据。
        # 例外：`tolerated=True` 的失败已被 `on_error=continue` 显式忽略（动作自己允许失败，
        # 如「取文本没命中」），不参与循环结论，否则外层会把它当成致命失败。
        failed = next((r for r in results if not r.success and not r.tolerated), None)
        return ActionResult(
            success=failed is None,
            error=failed.error if failed else None,
            # iterations = 实际轮次（此前误用循环体内步骤结果条数，3 轮 × 4 步会报成 12）
            data={"iterations": rounds, "steps": len(results)},
            action_id=self.action_id,
            action_name="loop",
        )

    async def _loop_by_items(
        self, scope: Scope, executor: ActionExecutor, items: list[Any]
    ) -> tuple[list[ActionResult], int]:
        """遍历 items 列表执行循环体（返回步骤结果与实际轮次）"""
        results = []
        rounds = 0
        for i, item_value in enumerate(items):
            if scope.is_cancelled():
                break
            scope.set(self.loop_index_var, i)
            scope.set(self.loop_item_var, item_value)
            # 列表循环的轮次分母一开始就已知：逐轮上报
            await self._report_iteration(scope, i, len(items))
            if self._should_break(scope):
                break
            if self._should_continue(scope):
                continue
            mapped = self._resolve_param_mapping(scope)
            body = self._inject_mapped_params(self.body, mapped)
            self._randomize_trailing_wait(body)
            ir = await body.execute(scope, executor, body_of_loop=True)
            results.extend(ir)
            rounds += 1
            if not await self._finish_iteration(scope, executor, ir, results):
                break
        return results, rounds

    async def _loop_by_count(
        self, scope: Scope, executor: ActionExecutor
    ) -> tuple[list[ActionResult], int]:
        results = []
        rounds = 0
        for i in range(self.count):
            if scope.is_cancelled():
                break
            scope.set(self.loop_index_var, i)
            await self._report_iteration(scope, i, self.count or 0)
            if self._should_break(scope):
                break
            if self._should_continue(scope):
                continue
            self._randomize_trailing_wait(self.body)
            ir = await self.body.execute(scope, executor, body_of_loop=True)
            results.extend(ir)
            rounds += 1
            if not await self._finish_iteration(scope, executor, ir, results):
                break
        return results, rounds

    async def _loop_by_while(
        self, scope: Scope, executor: ActionExecutor
    ) -> tuple[list[ActionResult], int]:
        from app.services.execution.actions.safe_eval import safe_evaluate_condition

        results = []
        rounds = 0
        i = 0
        while safe_evaluate_condition(self.loop_condition, scope.snapshot()):
            if scope.is_cancelled():
                break
            scope.set(self.loop_index_var, i)
            # while 循环总轮次不可知：上报 0，前端走不确定态
            await self._report_iteration(scope, i, 0)
            if self._should_break(scope):
                break
            if self._should_continue(scope):
                i += 1
                continue
            self._randomize_trailing_wait(self.body)
            ir = await self.body.execute(scope, executor, body_of_loop=True)
            results.extend(ir)
            rounds += 1
            if not await self._finish_iteration(scope, executor, ir, results):
                break
            i += 1
        return results, rounds

    async def _loop_by_until(
        self, scope: Scope, executor: ActionExecutor
    ) -> tuple[list[ActionResult], int]:
        from app.services.execution.actions.safe_eval import safe_evaluate_condition

        results = []
        rounds = 0
        i = 0
        while True:
            if scope.is_cancelled():
                break
            scope.set(self.loop_index_var, i)
            # until 循环的总轮次运行时才可知，上报 0（前端显示不确定态）
            await self._report_iteration(scope, i, 0)
            if self._should_break(scope):
                break
            if self._should_continue(scope):
                i += 1
                continue
            self._randomize_trailing_wait(self.body)
            ir = await self.body.execute(scope, executor, body_of_loop=True)
            results.extend(ir)
            rounds += 1
            if not await self._finish_iteration(scope, executor, ir, results):
                break
            if safe_evaluate_condition(self.loop_until, scope.snapshot()):
                break
            i += 1
        return results, rounds

    async def _finish_iteration(
        self,
        scope: Scope,
        executor: ActionExecutor,
        ir: list[ActionResult],
        results: list[ActionResult],
    ) -> bool:
        """一轮结束的收尾（类似 finally）：失败处理 + 固定间隔等待。"""
        keep_going = True
        # 本轮最后一个动作失败才需要走失败处理；被 `on_error=continue` 容忍的失败不算本轮失败
        if ir and not ir[-1].success and not ir[-1].tolerated:
            keep_going = await self._handle_iteration_error(
                scope, executor, self.body, ir, results
            )
        if keep_going:
            await self._wait_iteration_gap()
            # 本轮因失败提前中断时，循环体末尾的「固定等待」会被跳过。
            # 为保证每条数据之间仍有节奏（降风控），这里补上同样的冷却；
            # 若已配置 iteration_wait_seconds，则以它为准，避免重复等待。
            if (
                len(ir) < len(self.body.steps)
                and self.iteration_wait_seconds <= 0
                and self._trailing_wait_seconds() > 0
            ):
                await asyncio.sleep(self._trailing_wait_seconds())
        return keep_going

    async def _wait_iteration_gap(self) -> None:
        """每轮结束后的固定等待：成功与失败都执行，用于控制整批节奏、降低风控。"""
        if self.iteration_wait_seconds > 0:
            await asyncio.sleep(self.iteration_wait_seconds)

    def _randomize_trailing_wait(self, pipeline: Pipeline) -> float:
        """把循环体末尾「无 selector 的固定等待」改写为 10~30 秒随机值。

        - 只处理最后一个步骤是 `wait` 且无 selector 的情况（固定冷却）；
        - 配置了 `iteration_wait_seconds` 时不改写，避免与引擎冷却叠加；
        - 返回本次随机秒数（0 表示无需/未改写）。

        改写后，`wait` 动作的日志里能看到实际等待时长，行为可追溯。
        """
        if self.iteration_wait_seconds > 0:
            return 0.0
        # 只对「遍历数据列表」的循环做随机冷却；
        # fixed_count 常被用作轮询等待（如最多 35 次检查登录），把每轮 1s 放大到
        # 10~30s 会让它变成十几分钟的假死，必须保持原样。
        if not self.loop_items_var:
            return 0.0
        if not pipeline.steps:
            return 0.0
        last = pipeline.steps[-1]
        if not isinstance(last, AtomicStep):
            return 0.0
        if str(getattr(last, "action_id", "")) != "wait":
            return 0.0
        params = dict(getattr(last, "params", None) or {})
        if params.get("selector"):
            return 0.0
        seconds = random.randint(
            self.TAIL_WAIT_MIN_SECONDS, self.TAIL_WAIT_MAX_SECONDS
        )
        params["timeout"] = seconds * 1000
        last.params = params
        return float(seconds)

    def _trailing_wait_seconds(self) -> float:
        """循环体末尾若是「无 selector 的固定等待」，取它的超时秒数作为补偿冷却。

        仅用于失败提前中断、尾部等待没执行到的场景（成功时尾步骤自己会等）。
        """
        if not self.body.steps:
            return 0.0
        last = self.body.steps[-1]
        if not isinstance(last, AtomicStep):
            return 0.0
        if str(getattr(last, "action_id", "")) != "wait":
            return 0.0
        params = getattr(last, "params", None) or {}
        if params.get("selector"):
            return 0.0
        try:
            timeout_ms = float(params.get("timeout") or 0)
        except (TypeError, ValueError):
            return 0.0
        # 上限 60s，避免异常配置把循环卡住
        return min(max(timeout_ms / 1000.0, 0.0), 60.0)

    async def _handle_iteration_error(
        self,
        scope: Scope,
        executor: ActionExecutor,
        body: Pipeline,
        ir: list[ActionResult],
        results: list[ActionResult],
    ) -> bool:
        """处理单次迭代失败，返回 True=继续下一轮，False=break。

        根据 self.on_error 策略：
            "continue" → 跳过失败迭代，继续下一轮
            "retry"    → 重试 self.retry 次，成功则继续，失败则根据配置决定
            "stop"     → 停止循环
        """
        last_result = ir[-1]

        # ── 先跑 on_error_branch ──
        if self.on_error_branch and self.on_error_branch.steps:
            scope.push()
            scope.set("last_error", last_result.error or "未知错误")
            scope.set("last_output", last_result.data)
            logger.info(f"循环迭代失败，执行 on_error_branch")
            eb_results = await self.on_error_branch.execute(scope, executor)
            results.extend(eb_results)
            scope.pop()

        # ── 策略 ──
        if self.on_error == OnErrorEnum.CONTINUE:
            # 每轮固定间隔由 _finish_iteration 统一处理（成功/失败都等）
            logger.warning(f"循环迭代失败但继续: {last_result.error}")
            # 循环级 continue 同样属于「显式容忍」：不计入整次运行的失败结论
            last_result.tolerated = True
            return True

        if self.on_error == OnErrorEnum.RETRY and self.retry > 0:
            for retry_i in range(self.retry):
                logger.info(f"循环重试 ({retry_i + 1}/{self.retry})")
                retry_ir = await body.execute(scope, executor, body_of_loop=True)
                results.extend(retry_ir)
                if retry_ir and retry_ir[-1].success:
                    # 重试已成功：此前那次失败属于「已恢复」，不参与循环/运行结论
                    last_result.tolerated = True
                    return True
            # 重试全部失败，根据 retry 后行为：继续下一轮还是停止
            if self.retry > 0:
                return False  # 重试耗尽 → break

        # 默认 stop → break
        return False


@dataclass
class IfElseStep(StepNode):
    """条件分支步骤 — 根据条件选择执行 true/false 子管道。

    字段:
        condition_rule: ConditionRule — 分支条件
        true_body:      Pipeline | None
        false_body:     Pipeline | None
    """

    condition_rule: ConditionRule | None = None
    #: 表达式条件（与 condition_rule 二选一）：支持变量之间的比较，
    #: 如 `self_nickname in latest_comment_user`。走 safe_evaluate_condition
    #: 的 AST 白名单，不使用 eval。
    expression: str | None = None
    true_body: Pipeline | None = None
    false_body: Pipeline | None = None

    async def execute(self, scope: Scope, executor: ActionExecutor) -> ActionResult:
        try:
            if self.expression:
                from app.services.execution.actions.safe_eval import (
                    safe_evaluate_condition,
                )

                take_true = safe_evaluate_condition(
                    self.expression, scope.snapshot()
                )
            else:
                take_true = (
                    evaluate_rule(self.condition_rule, scope.snapshot())
                    if self.condition_rule is not None
                    else False
                )
        except ConditionEvaluateError as e:
            logger.warning(f"if_else 条件评估失败: {e}")
            take_true = False

        selected = self.true_body if take_true else self.false_body
        branch_name = "true" if take_true else "false"
        if selected is None:
            return ActionResult(
                success=True, action_id=self.action_id, action_name="if_else"
            )

        results = await selected.execute(scope, executor)
        branch_failed = results and not results[-1].success

        if branch_failed:
            return await self._handle_branch_error(
                scope, executor, selected, results, branch_name
            )

        return ActionResult(
            success=True,
            data={"branch": branch_name, "results": results},
            action_id=self.action_id,
            action_name="if_else",
        )

    async def _handle_branch_error(
        self,
        scope: Scope,
        executor: ActionExecutor,
        selected: Pipeline,
        results: list[ActionResult],
        branch_name: str,
    ) -> ActionResult:
        """分支失败处理：先跑 on_error_branch，再按 on_error 策略返回"""
        last_result = results[-1]

        if self.on_error_branch and self.on_error_branch.steps:
            scope.push()
            scope.set("last_error", last_result.error or "未知错误")
            scope.set("last_output", last_result.data)
            logger.info(f"if_else 分支 {branch_name} 失败，执行 on_error_branch")
            self.on_error_branch.execute(scope, executor)  # fire-and-forget 日志
            scope.pop()

        if self.on_error == OnErrorEnum.CONTINUE:
            logger.warning(
                f"if_else 分支 {branch_name} 失败但忽略: {last_result.error}"
            )
            return ActionResult(
                success=True,
                data={"branch": branch_name, "results": results},
                action_id=self.action_id,
                action_name="if_else",
            )

        if self.on_error == OnErrorEnum.RETRY and self.retry > 0:
            for retry_i in range(self.retry):
                logger.info(
                    f"if_else 重试分支 {branch_name} ({retry_i + 1}/{self.retry})"
                )
                retry_results = await selected.execute(scope, executor)
                results.extend(retry_results)
                if retry_results and retry_results[-1].success:
                    return ActionResult(
                        success=True,
                        data={"branch": branch_name, "results": results},
                        action_id=self.action_id,
                        action_name="if_else",
                    )

        return ActionResult(
            success=False,
            error=f"if_else 分支 {branch_name} 执行失败: {last_result.error}",
            data={"branch": branch_name, "results": results},
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
        *,
        top_level: bool = False,
        body_of_loop: bool = False,
    ) -> list[ActionResult]:
        """执行步骤序列。

        Args:
            top_level: 是否为工作流的顶层管道。只有顶层才把步骤计入运行进度，
                循环体 / 分支体等嵌套管道不重复计数（嵌套进度由 LoopStep 单独上报）。
            body_of_loop: 是否为循环的循环体（**任意嵌套层**）。为 True 时把本轮内的
                动作上报到「循环内动作」维度（第二条进度条，只有最外层生效）
                并发运行消息（每层都发），不计入顶层步骤。
        """
        results: list[ActionResult] = []
        reporter = scope.progress if top_level else None
        body_reporter = (
            scope.progress if body_of_loop and scope.progress is not None else None
        )
        # 本循环体所在层级：顶层步骤为 0，最外层循环体为 1，再内一层为 2……
        body_depth = scope.loop_nesting
        body_total = len(self.steps)

        for index, step in enumerate(self.steps):
            step_started = False
            outcome_success = False
            step_error: str | None = None
            # 循环体内动作的耗时（供运行消息展示；顶层步骤的耗时由上报器自己记）
            body_started_at = 0.0
            # 步骤的最终结果：供进度上报器渲染「返回内容」明细（SSE 运行消息 detail）
            outcome_result: ActionResult | None = None
            try:
                # 协作式停止：每个步骤边界检查一次，命中则停止后续步骤（见 cancellation.py）
                if scope.is_cancelled():
                    logger.warning("检测到停止请求，终止后续步骤执行")
                    break

                if not step.should_execute(scope):
                    logger.info(f"跳过步骤 {step.action_id}（条件不满足）")
                    continue

                logger.info(f"▶ 执行步骤: {step.action_id}")
                if reporter is not None:
                    await reporter.step_started(index, step.action_id)
                    step_started = True
                if body_reporter is not None:
                    # 循环体内动作开始：进度（仅最外层）+ 一条「循环内」运行消息（每层都发）
                    await body_reporter.body_step_started(
                        index, body_total, step.action_id, depth=body_depth
                    )
                start = time.time()
                body_started_at = start
                result = await step.execute(scope, executor)
                result.execution_time = time.time() - start
                result.action_id = step.action_id
                outcome_result = result
                results.append(result)
                outcome_success = result.success
                step_error = None if result.success else result.error


                if result.success:
                    continue  # 成功，进入下一步

                # ── 失败处理：先跑 on_error_branch（回退/清理/告警）──
                if step.on_error_branch and step.on_error_branch.steps:
                    scope.push()
                    scope.set("last_error", result.error or "未知错误")
                    scope.set("last_output", result.data)
                    logger.info(f"执行 {step.action_id} 的错误处理分支")
                    error_results = await step.on_error_branch.execute(scope, executor)
                    results.extend(error_results)
                    scope.pop()

                # ── 根据 on_error 策略决定后续行为 ──
                if step.on_error == OnErrorEnum.CONTINUE:
                    logger.warning(
                        f"步骤 {step.action_id} 失败但继续执行: {result.error}"
                    )
                    # 显式容忍：仍记失败（日志/计数），但不参与「整次运行是否失败」的结论
                    result.tolerated = True
                    continue

                if step.on_error == OnErrorEnum.RETRY and step.retry > 0:
                    for retry_i in range(step.retry):
                        logger.info(
                            f"重试 {step.action_id} ({retry_i + 1}/{step.retry})"
                        )
                        result = await step.execute(scope, executor)
                        outcome_result = result
                        if result.success:
                            break
                    results[-1] = result
                    outcome_success = result.success
                    step_error = None if result.success else result.error
                    if result.success:
                        continue  # 重试成功，进入下一步

                # on_error="stop"（默认）或重试全部失败 → 停止
                break

            except Exception as e:
                logger.error(f"步骤异常: {step.action_id} - {e}")
                outcome_result = ActionResult(
                    success=False,
                    error=str(e),
                    execution_time=0,
                    action_id=step.action_id,
                    action_name=step.action_id,
                )
                results.append(outcome_result)
                break
            finally:
                # 只在真正开始执行后才计数（条件跳过不计入进度）；
                # 放在 finally 里保证成功 / 失败 / 异常 / 重试都只上报一次终态。
                if reporter is not None and step_started:
                    await reporter.step_finished(
                        outcome_success, step_error, result=outcome_result
                    )
                if body_reporter is not None and body_started_at:
                    await body_reporter.body_step_finished(
                        index + 1,
                        body_total,
                        step.action_id,
                        outcome_success,
                        step_error,
                        result=outcome_result,
                        duration_ms=(time.time() - body_started_at) * 1000,
                        depth=body_depth,
                    )

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
            action_type = PipelineBuilder._get_attr(
                s, "action_type"
            ) or PipelineBuilder._get_attr(s, "action_id", "")
            condition = PipelineBuilder._get_attr(s, "condition")

            # LOOP
            if action_type == BuiltinActionType.LOOP:
                children = PipelineBuilder._get_children(s)
                count = getattr(s, "count", None) or PipelineBuilder._get_param(
                    s, "count"
                )
                loop_while = getattr(
                    s, "loop_while", None
                ) or PipelineBuilder._get_param(s, "loop_while")
                loop_until = getattr(
                    s, "loop_until", None
                ) or PipelineBuilder._get_param(s, "loop_until")
                loop_source = (
                    PipelineBuilder._get_param(s, "loop_source") or "fixed_count"
                )
                loop_items_var = PipelineBuilder._get_param(s, "loop_items_var")
                loop_item_var = (
                    PipelineBuilder._get_param(s, "loop_item_var") or "loop_item"
                )
                loop_index_var = (
                    PipelineBuilder._get_param(s, "loop_index_var") or "loop_index"
                )
                param_mapping = PipelineBuilder._get_param(s, "param_mapping")
                # 循环默认「单条失败继续下一条」：批量处理里一条失败不应中断整批。
                # 注意模型字段有默认值 stop，必须看「用户是否显式设置」
                # （model_fields_set），否则永远拿到默认 stop。
                explicit_on_error = (
                    PipelineBuilder._get_attr(s, "on_error")
                    if "on_error" in getattr(s, "model_fields_set", set())
                    else None
                )
                on_error = OnErrorEnum(explicit_on_error or "continue")
                on_error_raw = PipelineBuilder._get_attr(s, "on_error_branch")
                on_error_branch = (
                    PipelineBuilder.build(on_error_raw) if on_error_raw else None
                )
                break_raw = PipelineBuilder._get_param(s, "break_condition")
                continue_raw = PipelineBuilder._get_param(s, "continue_condition")
                break_condition = (
                    ConditionRule.model_validate(break_raw)
                    if isinstance(break_raw, dict)
                    else break_raw
                )
                continue_condition = (
                    ConditionRule.model_validate(continue_raw)
                    if isinstance(continue_raw, dict)
                    else continue_raw
                )

                # 确定迭代次数
                if loop_source == "fixed_count":
                    count = count or 1
                else:
                    count = None  # variable/expression 模式不设 count

                # 向后兼容旧字段
                old_loop_count = getattr(
                    s, "loop_count", None
                ) or PipelineBuilder._get_param(s, "loop_count")
                if count is None and old_loop_count is not None:
                    count = old_loop_count

                nodes.append(
                    LoopStep(
                        action_id=action_id,
                        condition=condition,
                        on_error=on_error,
                        on_error_branch=on_error_branch,
                        body=PipelineBuilder.build(children)
                        if children
                        else Pipeline([]),
                        count=count,
                        loop_condition=loop_while,
                        loop_until=loop_until,
                        loop_items_var=loop_items_var
                        if loop_source != "fixed_count"
                        else None,
                        loop_item_var=loop_item_var,
                        loop_index_var=loop_index_var,
                        param_mapping=param_mapping,
                        break_condition=break_condition,
                        continue_condition=continue_condition,
                        iteration_wait_seconds=float(
                            PipelineBuilder._get_param(s, "iteration_wait_seconds") or 0
                        ),
                    )
                )

            # IF_ELSE
            elif action_type == BuiltinActionType.IF_ELSE:
                true_raw = PipelineBuilder._get_param(s, "TrueBranch")
                false_raw = PipelineBuilder._get_param(s, "FalseBranch")
                rule_raw = PipelineBuilder._get_param(s, "condition")
                rule = (
                    ConditionRule.model_validate(rule_raw)
                    if isinstance(rule_raw, dict)
                    else rule_raw
                )
                # 表达式条件：支持变量之间比较（结构化条件只能比字面量）
                expression_raw = PipelineBuilder._get_param(s, "expression")
                on_error = OnErrorEnum(
                    PipelineBuilder._get_attr(s, "on_error", "stop") or "stop"
                )
                on_error_raw = PipelineBuilder._get_attr(s, "on_error_branch")
                on_error_branch = (
                    PipelineBuilder.build(on_error_raw) if on_error_raw else None
                )

                nodes.append(
                    IfElseStep(
                        action_id=action_id,
                        condition=condition,
                        on_error=on_error,
                        on_error_branch=on_error_branch,
                        condition_rule=rule,
                        expression=(
                            expression_raw
                            if isinstance(expression_raw, str) and expression_raw.strip()
                            else None
                        ),
                        true_body=PipelineBuilder.build(true_raw) if true_raw else None,
                        false_body=PipelineBuilder.build(false_raw)
                        if false_raw
                        else None,
                    )
                )

            # Atomic / Composite (default)
            else:
                params = PipelineBuilder._get_attr(s, "params", {}) or {}
                input_vars = PipelineBuilder._get_attr(s, "input_vars", None) or {}
                output_vars = PipelineBuilder._get_attr(s, "output_vars", None) or []
                retry = PipelineBuilder._get_attr(s, "retry", 0) or 0
                on_error = OnErrorEnum(
                    PipelineBuilder._get_attr(s, "on_error", "stop") or "stop"
                )
                on_error_raw = PipelineBuilder._get_attr(s, "on_error_branch")
                on_error_branch = (
                    PipelineBuilder.build(on_error_raw) if on_error_raw else None
                )
                nodes.append(
                    AtomicStep(
                        action_id=action_id,
                        condition=condition,
                        retry=retry,
                        on_error=on_error,
                        on_error_branch=on_error_branch,
                        params=params
                        if isinstance(params, dict)
                        else params.model_dump(),
                        input_vars=input_vars if isinstance(input_vars, dict) else {},
                        output_vars=output_vars,
                    )
                )

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
        """安全获取 params 中的字段（兼容 dict 和模型）。

        注意：嵌套子步骤（loop 里的 if_else / loop）从 DB JSON 读回时**是 dict**，
        不能用 ``getattr(step, "params")`` —— dict 没有 params 属性，会静默拿到 {}，
        导致条件为 None、TrueBranch/FalseBranch 编译成空分支，
        表现为「循环里的分支整体不执行」（如官方抽奖不转发、预约不写去重记录）。
        """
        if isinstance(step, dict):
            params = step.get("params") or {}
        else:
            params = getattr(step, "params", {}) or {}
        if isinstance(params, dict):
            return params.get(key, default)
        return getattr(params, key, default)
