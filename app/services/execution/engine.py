"""
Execution Engine — 操作执行引擎

重构后的架构：

    数据结构:
        Scope       — 变量作用域栈（链式查找，push/pop 隔离）
        Pipeline    — 步骤序列 IR（编译自 WorkflowStep 列表）
        StepNode    — sealed union: AtomicStep | LoopStep | IfElseStep

    算法:
        Pipeline.execute(scope):    left-fold → 依次执行 StepNode
        Scope.resolve_params():     DFS 遍历参数树 → 替换 {{var}}
        Scope.query(key):           栈顶 → 栈底链式查找

    数据流:
        scope = Scope(initial_vars)
              ↓ (同一引用)
        BaseAction.variables = scope.current
              ↓ _merge_output_vars 直接写入
        scope.set("up_nick_name", "自豪一级棒")
              ↓ 下一步 scope.resolve_params()
        "{{up_nick_name}}" → "自豪一级棒"   （O(1) 查找）
"""

from app.services.execution.pipeline import PipelineBuilder, Pipeline, ActionExecutor
from app.services.execution.cancellation import cancellation_registry
from app.services.execution.actions.base import BaseAction, ActionResult
from app.services.execution.scope import Scope, TemplateResolveError
from botright.playwright_mock.page import Page
import time
import asyncio
import traceback
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List

from loguru import logger

if TYPE_CHECKING:
    from app.services.execution.workflow_progress import WorkflowRunProgressReporter

from app.models.database.log.models import ActionLogSourceEnum, ActionLogStatusEnum
from app.services.execution.action_logger import (
    ActionLogContext,
    new_execution_id,
    resolve_log_option,
    save_action_log,
)
from app.models.database.workflow.models import WorkflowStep
from app.services.execution.crud_service import (
    action_crud_svr,
    workflow_crud_svr,
)
from app.models.execution.action_params import (
    ActionMetadata,
    BaseWorkflowStep,
    ActionLogOption,
    BuiltinActionType,
)
from app.models.execution.condition_models import ConditionRule
from app.services.execution.actions.control_flow import (
    CompositeAction as CompositeActionClass,
)
from app.services.execution.actions.all_actions import (
    get_action_class,
    get_action_metadata,
)
from app.services.execution.actions.base import (
    CustomActionResolver,
    set_custom_action_resolver,
)
from app.services.execution.action_registry import action_registry
from app.services.RPA_browser.session.live_service import live_service
from app.services.RPA_browser.session.page_registry import page_registry
from app.models.execution.request_params import (
    ExecutionRequest,
    ActionExecutionRequest,
    params_to_dict,
)

# 自定义（复合）动作解析槽位：控制流策略层（AtomicStrategy）经 base 的
# CustomActionResolver 查找，从而无需 import action_registry →
# all_actions → control_flow 这一会成环的依赖链。
set_custom_action_resolver(
    CustomActionResolver(
        get_action_class=action_registry.get_action_class_for_user,
        get_steps=action_registry.get_custom_action_steps,
    )
)


class ExecutionEngine:
    """执行引擎（无状态，所有上下文由 Scope + Pipeline 携带）"""

    def __init__(self, **kwargs):
        self.max_depth = kwargs.get("max_depth")
        self.default_timeout = kwargs.get("timeout")

    # ═══════════════ 公开 API ═══════════════════════════════════

    async def execute_action(
        self,
        req: ActionExecutionRequest,
        *,
        session_id: str,
        browser_id: str,
        page: Page | None = None,
        execution_id: str | None = None,
        parent_execution_id: str | None = None,
        log_source: ActionLogSourceEnum = ActionLogSourceEnum.ACTION,
        workflow_log_enabled: bool | None = None,
        workflow_log_debug: bool | None = None,
    ) -> ActionResult:
        """执行单个操作。

        路由 /actions/execute 直接调用此方法。

        Args:
            workflow_log_enabled: 工作流级日志开关（三态）；None 表示未经工作流
                调度，按动作自身配置 / 项目兜底解析（既有默认行为）。
            workflow_log_debug: 工作流级调试模式（用户在工作流上开启的
                ``UserWorkflow.log_debug``）；True 时优先于开关，强制全量采集。
        """
        if page is None:
            raise ValueError("必须提供 page 参数")

        # 自动化运行期间占用会话（pin），禁止闲置降级/关闭；结束时刷新活跃再解除
        live_service.pin(req.mid, browser_id)
        exec_id = (
            execution_id
            or getattr(req, "execution_id", "")
            or new_execution_id()
        )
        try:
            # 构建 Scope（兼容旧 req.variables dict）
            scope = Scope(req.variables)
            output_vars = getattr(req, "output_vars", None) or []

            return await self._run_action(
                action_id=req.action_id,
                params=params_to_dict(req.params),
                scope=scope,
                output_vars=output_vars,
                session_id=session_id,
                browser_id=browser_id,
                page=page,
                mid=req.mid,
                auth_headers=getattr(req, "auth_headers", {}) or {},
                execution_id=exec_id,
                parent_execution_id=parent_execution_id,
                log_source=log_source,
                workflow_log_enabled=workflow_log_enabled,
                workflow_log_debug=workflow_log_debug,
            )
        finally:
            await live_service.touch(req.mid, browser_id, source="action")
            live_service.unpin(req.mid, browser_id)
            # 单动作 / 调试执行没有 run 级清理，这里兜底关掉本次打开的页面
            await self._cleanup_run_pages(req.mid, browser_id, exec_id)

    async def execute_steps(
        self,
        req: ExecutionRequest,
        *,
        steps: List[WorkflowStep],
        session_id: str,
        browser_id: str,
        page: Page,
        depth: int = 0,
        workflow_log_enabled: bool | None = None,
        workflow_log_debug: bool | None = None,
        progress: "WorkflowRunProgressReporter | None" = None,
        skip_steps: int = 0,
    ) -> List[ActionResult]:
        """执行工作流步骤列表。

        路由 /workflows/execute 调此方法。

        算法：
            1. 编译步骤列表 → Pipeline IR
            2. scope = Scope(req.variables)  （所有步骤共享同一引用）
            3. pipeline.execute(scope, executor)  （left-fold）

        Args:
            workflow_log_enabled: 工作流粒度的日志采集开关（三态：
                ``UserWorkflow.log_enabled``）。``None``=未设置（听从动作级 /
                项目兜底）；``True/False``=本次运行显式开/关，压制动作级与项目级。
                单操作执行不传，行为保持不变。
            workflow_log_debug: 工作流级调试模式（三态：
                ``UserWorkflow.log_debug``）。``True``=本次运行强制全量采集
                （入参/结果/变量全记、成功也记、payload 不截断），优先于开关；
                ``None``=未设置，按既有链路解析。
            progress: 运行进度上报器（仅工作流调度执行注入）；None 时不做任何埋点。
            skip_steps: 断点恢复起点：跳过前 N 个已完成步骤，从第 N 个（0 基）继续执行。
                被跳过的步骤不再产生副作用，其成功计数已由旧运行继承。

        时间复杂度：O(N)，N 为步骤数（不含嵌套）。
        """
        # 自动化运行期间占用会话（pin），禁止闲置降级/关闭；结束时刷新活跃再解除
        live_service.pin(req.mid, browser_id)
        exec_id = getattr(req, "execution_id", "") or new_execution_id()
        try:
            req_auth_headers = getattr(req, "auth_headers", {}) or {}
            workflow_id = getattr(req, "workflow_id", None)

            scope = Scope(req.variables)
            scope.set("execute_steps_func", self.execute_steps)
            # 协作式停止钩子：按本次运行的 execution_id 查询取消注册表，
            # 使 Pipeline / LoopStep 能在步骤边界响应「手动停止」（见 cancellation.py）
            scope.cancel_check = lambda: cancellation_registry.is_cancelled(exec_id)
            # 运行进度上报器：顶层步骤由 Pipeline 上报，循环轮次由 LoopStep 上报
            scope.progress = progress

            remaining_steps = steps[skip_steps:] if skip_steps > 0 else steps
            pipeline = PipelineBuilder.build(remaining_steps)

            async def executor(
                action_id: str,
                params: dict,
                scope: Scope,
                output_vars: list[str],
            ) -> ActionResult:
                return await self._run_action(
                    action_id=action_id,
                    params=params,
                    scope=scope,
                    output_vars=output_vars,
                    session_id=session_id,
                    browser_id=browser_id,
                    page=page,
                    mid=req.variables.get("mid", req.mid),
                    auth_headers=req_auth_headers,
                    execution_id=exec_id,
                    depth=depth,
                    workflow_id=workflow_id,
                    log_source=ActionLogSourceEnum.WORKFLOW,
                    workflow_log_enabled=workflow_log_enabled,
                    workflow_log_debug=workflow_log_debug,
                )

            # top_level=True：只有顶层管道把步骤计入运行进度（嵌套循环体/分支体不重复计数）
            return await pipeline.execute(scope, executor, top_level=True)
        finally:
            await live_service.touch(req.mid, browser_id, source="workflow")
            live_service.unpin(req.mid, browser_id)
            # 工作流路径通常已由 run_workflow 清理（close_owned 幂等）；
            # 内联步骤执行（MCP 调试 / 试跑）在这里兜底
            await self._cleanup_run_pages(req.mid, browser_id, exec_id)

    @staticmethod
    async def _cleanup_run_pages(
        mid: int | str, browser_id: str | int, exec_id: str
    ) -> None:
        """关闭本次执行按别名打开的页面（失败不影响主流程）。"""
        try:
            closed = await page_registry.close_owned(
                (int(mid), int(browser_id or 0)), exec_id
            )
            if closed:
                logger.info(f"[ExecutionEngine] 执行结束关闭页面: {closed}")
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响结果
            logger.warning(f"[ExecutionEngine] 清理执行页面失败: {exc}")

    # ═══════════════ 核心执行 ─────────────────────────────────

    async def _run_action(
        self,
        *,
        action_id: str,
        params: dict,
        scope: Scope,
        output_vars: list[str],
        session_id: str,
        browser_id: str,
        page: Page,
        mid: int | str = 0,
        auth_headers: dict[str, str] | None = None,
        execution_id: str | None = None,
        parent_execution_id: str | None = None,
        depth: int = 0,
        workflow_id: str | None = None,
        log_source: ActionLogSourceEnum = ActionLogSourceEnum.ACTION,
        workflow_log_enabled: bool | None = None,
        workflow_log_debug: bool | None = None,
    ) -> ActionResult:
        """执行单个 action 并按用户配置采集操作日志。

        这是所有浏览器操作执行的唯一入口（单操作 / 工作流步骤都会经过），
        因此在此处统一埋点，保证日志采集不遗漏。

        Args:
            workflow_log_enabled: 工作流级日志开关（三态）。None 表示未经工作流
                调度，按动作自身配置 / 项目兜底解析（单操作执行的默认行为）。
            workflow_log_debug: 工作流级调试模式（三态）。True 时优先于开关，
                强制全量采集；None 表示未经工作流调度 / 未开启。
        """
        exec_id = execution_id or new_execution_id()
        log_ctx = ActionLogContext(
            mid=mid,
            action_id=action_id,
            action_name=action_id,
            action_type=action_id,
            source=log_source,
            execution_id=exec_id,
            parent_execution_id=parent_execution_id,
            depth=depth,
            workflow_id=workflow_id,
            browser_id=str(browser_id or ""),
            session_id=str(session_id or ""),
            page=page,
            started_at=datetime.now(),
        )

        result = await self._run_action_core(
            action_id=action_id,
            params=params,
            scope=scope,
            output_vars=output_vars,
            session_id=session_id,
            browser_id=browser_id,
            page=page,
            mid=mid,
            auth_headers=auth_headers,
            execution_id=exec_id,
            depth=depth,
            workflow_id=workflow_id,
            log_ctx=log_ctx,
            workflow_log_enabled=workflow_log_enabled,
            workflow_log_debug=workflow_log_debug,
        )

        log_ctx.action_name = str(result.action_name or action_id)
        log_ctx.params = result.replaced_params or dict(params)
        log_ctx.variables = scope.snapshot()
        status = (
            ActionLogStatusEnum.TIMEOUT
            if (not result.success and (result.error or "").strip() == "操作超时")
            else None
        )
        # 失败强制留痕：`on_error=continue` 会把失败降级成「步骤成功」，若再被日志开关
        # 压制，运行记录里就完全查不到「为什么没干活」。成功仍按配置决定是否记录。
        await save_action_log(
            log_ctx, result, status=status, force=not result.success
        )
        return result

    async def _run_action_core(
        self,
        *,
        action_id: str,
        params: dict,
        scope: Scope,
        output_vars: list[str],
        session_id: str,
        browser_id: str,
        page: Page,
        mid: int | str = 0,
        auth_headers: dict[str, str] | None = None,
        execution_id: str = "",
        depth: int = 0,
        workflow_id: str | None = None,
        log_ctx: ActionLogContext | None = None,
        workflow_log_enabled: bool | None = None,
        workflow_log_debug: bool | None = None,
    ) -> ActionResult:
        """执行单个 action 的核心方法。

        流程：
            1. 查找 action 类
            2. 复合动作：注入 DB 子步骤
            3. 创建 BaseAction，variables = scope.current（同一引用）
            4. 参数验证
            5. action.execute() → _merge_output_vars 直接写入 scope

        注意：scope.current 和 action.variables 是同一 dict 引用，
        _merge_output_vars 写入 action.variables 即写入 scope，
        无需事后回写。
        """
        start = time.time()

        action_class = await action_registry.get_action_class_for_user(action_id)
        if not action_class:
            return self._fail(
                f"未找到操作: {action_id}",
                action_id,
                start,
                replaced_params=dict(params),
            )

        # 严格解析调用参数：模板变量缺失 / 值为 None / 点路径中断即报错终止本操作，
        # 禁止把未解析的 {{placeholder}} 字面量带入下游动作继续执行。
        # 注意：内联 steps 树不作为「本次调用参数」解析（其中含循环变量等仅在子步骤
        # 执行期才存在的模板，交由各子步骤执行时按作用域解析），否则会误报缺失。
        raw_params = dict(params)
        inline_steps = raw_params.pop("steps", None)
        # 并行分支里同样含「仅在子步骤执行期才存在」的模板，不能当本次调用参数解析
        inline_branches = raw_params.pop("branches", None)
        try:
            merged = scope.resolve_params(raw_params, strict=True)
        except TemplateResolveError as e:
            return self._fail(
                str(e), action_id, start, replaced_params=dict(raw_params)
            )
        if inline_steps is not None:
            merged["steps"] = inline_steps
        if inline_branches is not None:
            merged["branches"] = inline_branches
        if issubclass(action_class, CompositeActionClass):
            # 校验用户是否有权执行此复合操作（自身或公开）
            ca_model = await action_crud_svr.get_by_action_id(action_id)
            if ca_model and ca_model.mid != str(mid) and not ca_model.is_public:
                return self._fail(
                    f"无权访问操作: {action_id}",
                    action_id,
                    start,
                    replaced_params=merged,
                )

            db_steps = await action_registry.get_custom_action_steps(action_id)
            if db_steps and not merged.get("steps"):
                merged["steps"] = db_steps
            # 校验 steps 中引用的所有 ca_ 操作是否可访问，防止越权执行
            steps_to_validate = merged.get("steps", [])
            if steps_to_validate:
                await action_crud_svr.validate_steps_referenced_actions(
                    steps_to_validate, mid
                )
            # 自定义复合操作的显示名（如「参与B站抽奖」）：执行结果与操作日志
            # 统一用它，而非内置类型名「复合操作」——否则日志列表认不出是哪条动作
            composite_display_name = ca_model.name if ca_model else None
        else:
            composite_display_name = None

        # 页面别名：步骤可用 params.page 指定在哪个页面执行（见多页面计划书）
        resolved_page = page
        page_alias = merged.get("page")
        if page_alias:
            registry_key = (int(mid), int(browser_id or 0))
            if str(page_alias) == "main" and page_registry.get(registry_key, "main") is None:
                # 单动作执行 / 调试入口没有走 run_workflow 的绑定逻辑，这里兜底
                page_registry.bind_main(registry_key, page)
            resolved_page = page_registry.get(registry_key, str(page_alias))
            if resolved_page is None:
                return self._fail(
                    f"页面 {page_alias!r} 不存在或已关闭（请先 open_page 或检查别名）",
                    action_id,
                    start,
                    replaced_params=merged,
                )

        action: BaseAction = action_class.new_action(
            mid=mid,
            page=resolved_page,
            variables=scope.current,  # ← 同一引用，不拷贝
            params=merged,
            output_vars=output_vars,
        )
        # 解析本操作的日志采集配置，供内部子步骤继承（自定义操作按其基础配置，
        # 内置操作按项目兜底；也可由执行参数中的 log 选项显式覆盖）。
        # workflow_log_enabled 为工作流级总闸（三态）：非 None 时压制动作级与项目级。
        # workflow_log_debug 为工作流级调试模式（用户开关）：优先于总闸，强制全量采集。
        # 完整优先级见 action_logger.resolve_log_option 文档字符串。
        log_cfg: ActionLogOption | None = await resolve_log_option(
            mid, action_id, merged, None, workflow_log_enabled, workflow_log_debug
        )
        if log_ctx is not None:
            log_ctx.log_config = log_cfg
        # 透传本系统认证请求头，供 HTTP 请求类操作按需附带
        action.auth_headers = auth_headers or {}
        # 透传执行元信息，供复合操作内部子步骤的日志采集串联同一条链路
        action.exec_meta = {
            "execution_id": execution_id,
            "parent_execution_id": log_ctx.parent_execution_id if log_ctx else None,
            "browser_id": str(browser_id or ""),
            "session_id": str(session_id or ""),
            "workflow_id": workflow_id,
            "log_config": log_cfg,
        }

        ok, err = action.validate_params(merged)
        if not ok:
            return self._fail(
                err or "参数验证失败",
                action_id,
                start,
                action.action_name,
                replaced_params=merged,
            )

        try:
            logger.info(f"▶ 执行: {action.action_name} ({action_id})")
            result = await action.execute()

            result.execution_time = time.time() - start
            result.action_id = action_id
            result.action_name = composite_display_name or action.action_name
            result.replaced_params = merged

            return result

        except asyncio.TimeoutError:
            return self._fail(
                "操作超时", action_id, start, action.action_name, replaced_params=merged
            )

        except Exception as e:
            logger.error(f"操作失败: {action_id} - {e}")
            result = self._fail(
                str(e), action_id, start, action.action_name, replaced_params=merged
            )
            # 完整调用栈随执行结果进入 ActionLogRecord.logs：落库的 error_message
            # 只带 str(e) 看不出炸点，MCP 按 execution_id 排查需要 traceback。
            result.logs.append(traceback.format_exc())
            return result

    # ═══════════════ 工具方法 ─────────────────────────────────

    @staticmethod
    def _fail(
        error: str,
        action_id: str,
        start: float,
        action_name: str = "",
        replaced_params: dict | None = None,
    ) -> ActionResult:
        return ActionResult(
            success=False,
            error=error,
            execution_time=time.time() - start,
            action_id=action_id,
            action_name=action_name,
            replaced_params=replaced_params or {},
        )

    @staticmethod
    def _replace_params(params: Any, variables: dict) -> Any:
        """替换参数中的模板变量（兼容旧接口，内部委托给 Scope）。"""
        return Scope(variables).resolve_params(params)

    # ═══════════════ 预览与验证（兼容接口，不变） ══════════════

    def get_all_action_metadatas(self) -> List[ActionMetadata]:
        return action_registry.get_all_action_metadatas()

    @staticmethod
    async def preview_action(
        mid: int,
        action_id: str,
        params: dict | None = None,
        input_vars: dict | None = None,
    ) -> Dict[str, Any]:
        action_class = await action_registry.get_action_class_for_user(action_id)
        is_composite = action_class is not None and issubclass(
            action_class, CompositeActionClass
        )
        try:
            at = BuiltinActionType(action_id)
        except ValueError:
            at = BuiltinActionType.COMPOSITE

        metadata = at.metadata
        if not metadata:
            raise ValueError(f"未找到操作: {action_id}")

        params = params or {}
        accumulated: dict = dict(input_vars or {})

        if is_composite:
            child_steps = await action_registry.get_custom_action_steps(action_id)
            if child_steps:
                steps_preview = await ExecutionEngine._preview_steps_recursive(
                    child_steps, mid, accumulated
                )
            else:
                steps_preview = []
            return {
                "action_id": action_id,
                "action_name": metadata.name,
                "is_composite": True,
                "steps_preview": steps_preview,
                "replaced_params": params,
                "found_params": [],
                "preview_result": {
                    "total_steps": len(child_steps) if child_steps else 0
                },
                "preview_variables": dict(accumulated),
            }
        else:
            result = await ExecutionEngine._preview_action_recursive(
                action_id=action_id,
                params=params,
                mid=mid,
                variables=dict(accumulated),
                step_index=0,
            )
            return {
                "action_id": action_id,
                "action_name": metadata.name,
                "is_composite": False,
                "steps_preview": [result],
                "replaced_params": params,
                "found_params": [],
                "preview_result": {"single_action": True},
                "preview_variables": result.get("preview_variables", {}),
            }

    @staticmethod
    async def _preview_action_recursive(
        action_id,
        params,
        mid,
        variables,
        step_index=0,
        input_vars=None,
        output_vars=None,
    ):
        """
        预览辅助 — 保留以维持现有 preview 功能。
        TODO: 可后续用 Scope + Pipeline.preview() 替代。
        """
        base = {
            "step_index": step_index,
            "action_id": action_id,
            "original_params": dict(params),
            "replaced_params": dict(params),
            "input_vars": dict(input_vars or {}),
            "output_vars": list(output_vars or []),
            "preview_variables": {},
        }

        cls = await action_registry.get_action_class_for_user(action_id)
        if cls and issubclass(cls, CompositeActionClass):
            child_steps = await action_registry.get_custom_action_steps(action_id)
            if not child_steps:
                return base
            children = await ExecutionEngine._preview_steps_recursive(
                child_steps, mid, dict(variables)
            )
            child_vars = ExecutionEngine._collect_preview_vars(children)
            base["preview_variables"] = dict(child_vars)
            variables.update(child_vars)
            base["children"] = children
            return base

        if (
            params.get("TrueBranch") is not None
            or params.get("FalseBranch") is not None
        ):
            true_branch = params.get("TrueBranch", []) or []
            false_branch = params.get("FalseBranch", []) or []
            tc = (
                await ExecutionEngine._preview_steps_recursive(
                    true_branch, mid, dict(variables)
                )
                if true_branch
                else []
            )
            fc = (
                await ExecutionEngine._preview_steps_recursive(
                    false_branch, mid, dict(variables)
                )
                if false_branch
                else []
            )
            branch_vars: dict = {}
            for children in (fc, tc):
                for c in children:
                    branch_vars.update(c.get("preview_variables", {}))
            base["preview_variables"] = dict(branch_vars)
            variables.update(branch_vars)
            base["branches"] = {"true": tc, "false": fc}
            return base

        if params.get("loopBranch") is not None:
            loop_body = params.get("loopBranch", []) or []
            if not loop_body:
                return base
            loop_vars = dict(variables)
            loop_vars[params.get("loop_var", "item")] = None
            loop_vars["loop_index"] = 0
            lc = await ExecutionEngine._preview_steps_recursive(
                loop_body, mid, loop_vars
            )
            child_vars = ExecutionEngine._collect_preview_vars(lc)
            base["preview_variables"] = dict(child_vars)
            variables.update(child_vars)
            base["loop_preview"] = lc
            return base

        action_cls = get_action_class(action_id)
        if action_cls is not None:
            try:
                safe_params = action_cls._convert_params(params)
                action = action_cls.new_action(
                    mid=mid,
                    page=None,
                    variables=dict(variables),
                    params=safe_params,
                    output_vars=list(output_vars or []),
                )
                step_vars = action.preview().get("variables", {})
                base["preview_variables"] = dict(step_vars)
                variables.update(step_vars)
            except Exception:
                pass
        return base

    @staticmethod
    async def _preview_steps_recursive(steps, mid, variables):
        results = []
        for idx, step in enumerate(steps):
            aid = (
                step.get("action_id", "")
                if isinstance(step, dict)
                else getattr(step, "action_id", "")
            )
            sp = (
                step.get("params", {})
                if isinstance(step, dict)
                else getattr(step, "params", {})
            )
            siv = (
                step.get("input_vars", {})
                if isinstance(step, dict)
                else getattr(step, "input_vars", {})
            )
            sov = (
                step.get("output_vars", [])
                if isinstance(step, dict)
                else getattr(step, "output_vars", [])
            )
            r = await ExecutionEngine._preview_action_recursive(
                action_id=aid,
                params=sp,
                mid=mid,
                variables=dict(variables),
                step_index=idx,
                input_vars=siv or {},
                output_vars=sov or [],
            )
            results.append(r)
            variables.update(r.get("preview_variables", {}))
        return results

    @staticmethod
    def _collect_preview_vars(preview_results):
        all_vars = {}
        for r in preview_results:
            all_vars.update(r.get("preview_variables", {}))
        return all_vars

    @staticmethod
    async def validate_action(
        mid: int, action_id: str, params: dict | None = None
    ) -> Dict[str, Any]:
        action_class = await action_registry.get_action_class_for_user(action_id)
        if not action_class:
            raise ValueError(f"未找到操作: {action_id}")

        is_composite = issubclass(action_class, CompositeActionClass)
        try:
            at = BuiltinActionType(action_id)
        except ValueError:
            at = BuiltinActionType.COMPOSITE

        metadata = get_action_metadata(at)
        if not metadata:
            raise ValueError(f"未找到操作元数据: {action_id}")

        params = params or {}
        missing: list[str] = []
        errors: list[str] = []

        if is_composite:
            child_steps = await action_registry.get_custom_action_steps(action_id)
            if not child_steps:
                errors.append("复合操作没有子步骤")
        else:
            for param in metadata.parameters:
                if param.json_schema.get("required") and param.name not in params:
                    missing.append(param.name)

        return {
            "valid": len(missing) == 0 and len(errors) == 0,
            "action_id": action_id,
            "action_name": metadata.name,
            "missing_params": missing,
            "invalid_params": [],
            "errors": errors,
        }


execution_engine = ExecutionEngine()
