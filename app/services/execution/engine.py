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
from app.services.execution.actions.base import BaseAction, ActionResult
from app.services.execution.scope import Scope
from botright.playwright_mock.page import Page
import time
import asyncio
from typing import Any, Dict, List

from loguru import logger

from app.models.database.workflow.models import WorkflowStep
from app.services.execution.crud_service import action_crud_svr, plugin_crud_svr, workflow_crud_svr
from app.models.execution.action_params import ActionMetadata, PluginConfig, BaseWorkflowStep, BuiltinActionType
from app.models.execution.condition_models import ConditionRule
from app.services.execution.actions.control_flow import CompositeAction as CompositeActionClass
from app.services.execution.action_registry import action_registry
from app.models.execution.request_params import (
    ExecutionRequest,
    ActionExecutionRequest,
    params_to_dict,
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
        plugins: List[PluginConfig] | None = None,
    ) -> ActionResult[Any]:
        """执行单个操作（含插件钩子）。

        路由 /actions/execute 直接调用此方法。
        """
        if page is None:
            raise ValueError("必须提供 page 参数")

        # 构建 Scope（兼容旧 req.variables dict）
        scope = Scope(req.variables)
        output_vars = getattr(req, 'output_vars', None) or []

        return await self._run_action(
            action_id=req.action_id,
            params=params_to_dict(req.params),
            scope=scope,
            output_vars=output_vars,
            session_id=session_id,
            browser_id=browser_id,
            page=page,
            plugins=plugins or [],
            mid=req.mid,
            auth_headers=getattr(req, 'auth_headers', {}) or {},
        )

    async def execute_steps(
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
        """执行工作流步骤列表。

        路由 /workflows/execute 调此方法。

        算法：
            1. 编译步骤列表 → Pipeline IR
            2. scope = Scope(req.variables)  （所有步骤共享同一引用）
            3. pipeline.execute(scope, executor)  （left-fold）

        时间复杂度：O(N)，N 为步骤数（不含嵌套）。
        """
        scope = Scope(req.variables)
        scope.set("execute_steps_func", self.execute_steps)
        req_auth_headers = getattr(req, 'auth_headers', {}) or {}

        pipeline = PipelineBuilder.build(steps)

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
                plugins=plugins or [],
                mid=req.variables.get("mid", req.mid),
                auth_headers=req_auth_headers,
            )

        return await pipeline.execute(scope, executor)

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
        plugins: List[PluginConfig],
        mid: int | str = 0,
        auth_headers: dict[str, str] | None = None,
    ) -> ActionResult[Any]:
        """执行单个 action 的核心方法。

        流程：
            1. 查找 action 类
            2. 复合动作：注入 DB 子步骤
            3. 创建 BaseAction，variables = scope.current（同一引用）
            4. 参数验证
            5. before_action 插件
            6. action.execute() → _merge_output_vars 直接写入 scope
            7. after_action 插件
            8. on_success / on_error 钩子

        注意：scope.current 和 action.variables 是同一 dict 引用，
        _merge_output_vars 写入 action.variables 即写入 scope，
        无需事后回写。
        """
        start = time.time()

        action_class = await action_registry.get_action_class_for_user(action_id)
        if not action_class:
            return self._fail(f"未找到操作: {action_id}", action_id, start, replaced_params=dict(params))

        # InputAction 的变量缺失或为 None 时替换为空字符串
        resolve_default = "" if action_id == BuiltinActionType.INPUT else None
        merged = scope.resolve_params(dict(params), default=resolve_default)
        if issubclass(action_class, CompositeActionClass):
            # 校验用户是否有权执行此复合操作（自身或公开）
            ca_model = await action_crud_svr.get_by_action_id(action_id)
            if ca_model and ca_model.mid != str(mid) and not ca_model.is_public:
                return self._fail(f"无权访问操作: {action_id}", action_id, start, replaced_params=merged)

            db_steps = await action_registry.get_custom_action_steps(action_id)
            if db_steps and not merged.get("steps"):
                merged["steps"] = db_steps
            # 校验 steps 中引用的所有 ca_ 操作是否可访问，防止越权执行
            steps_to_validate = merged.get("steps", [])
            if steps_to_validate:
                await action_crud_svr.validate_steps_referenced_actions(steps_to_validate, mid)

        action: BaseAction = action_class.new_action(
            mid=mid,
            page=page,
            variables=scope.current,      # ← 同一引用，不拷贝
            params=merged,
            output_vars=output_vars,
        )
        # 透传本系统认证请求头，供 HTTP 请求类操作按需附带
        action.auth_headers = auth_headers or {}

        ok, err = action.validate_params(merged)
        if not ok:
            return self._fail(err or "参数验证失败", action_id, start, action.action_name, replaced_params=merged)

        plugins = plugins or []

        try:
            # ── before_action 插件（失败中断） ──
            if fail := await self._run_hooks("before_action", plugins, session_id, browser_id, page, scope, mid):
                return self._fail(f"前置插件失败: {fail}", action_id, start, action.action_name, replaced_params=merged)

            logger.info(f"▶ 执行: {action.action_name} ({action_id})")
            result = await action.execute()

            # ── after_action 插件（失败仅警告） ──
            if plugins:
                after_results = await self._execute_plugins(
                    plugins=plugins, hook_type="after_action",
                    session_id=session_id, browser_id=browser_id, page=page,
                    variables=scope.current, mid=mid, action_result=result,
                )
                for r in after_results:
                    if not r.success:
                        logger.warning(f"[Plugin] 后置插件失败: {r.error}")

            result.execution_time = time.time() - start
            result.action_id = action_id
            result.action_name = action.action_name
            result.replaced_params = merged

            await self._run_hooks(
                "on_success" if result.success else "on_error",
                plugins, session_id, browser_id, page, scope, mid,
            )

            return result

        except asyncio.TimeoutError:
            await self._run_hooks("on_timeout", plugins, session_id, browser_id, page, scope, mid)
            return self._fail("操作超时", action_id, start, action.action_name, replaced_params=merged)

        except Exception as e:
            logger.error(f"操作失败: {action_id} - {e}")
            await self._run_hooks("on_error", plugins, session_id, browser_id, page, scope, mid)
            return self._fail(str(e), action_id, start, action.action_name, replaced_params=merged)

    # ═══════════════ 插件系统 ─────────────────────────────────

    async def _execute_plugins(
        self,
        plugins: List[PluginConfig],
        hook_type: str,
        session_id: str,
        browser_id: str,
        page: Page,
        variables: dict | None = None,
        mid: int | str = 0,
        action_result: ActionResult[Any] | None = None,
    ) -> List[ActionResult]:
        """执行匹配 hook_type 的插件列表。"""
        plugin_results: List[ActionResult] = []
        filtered = [p for p in plugins if p.hook_type == hook_type]
        if not filtered:
            return plugin_results

        for pc in filtered:
            if not pc.plugin_id:
                continue
            try:
                info = await plugin_crud_svr.get_by_plugin_id(pc.plugin_id)
                if not info or not info.custom_action_id:
                    continue

                logger.info(f"[Plugin] {info.name} (hook={hook_type})")
                p_start = time.time()

                p_vars = dict(variables or {})
                if action_result:
                    p_vars["_plugin_action_result"] = {
                        "success": action_result.success,
                        "data": action_result.data,
                        "error": action_result.error,
                    }
                p_vars["_plugin_config"] = pc.config_params

                p_req = ActionExecutionRequest(
                    mid=mid,
                    browser_id=int(browser_id) if str(browser_id).isdigit() else 0,
                    action_id=info.custom_action_id,
                    params=pc.config_params,
                    variables=p_vars,
                )
                pr = await self.execute_action(
                    p_req, session_id=session_id, browser_id=browser_id, page=page,
                )
                pr.execution_time = time.time() - p_start
                plugin_results.append(pr)
            except Exception as e:
                logger.error(f"[Plugin] 失败: {e}")
                plugin_results.append(ActionResult(
                    success=False, error=str(e),
                    execution_time=0,
                    action_id=pc.plugin_id,
                    action_name=f"Plugin: {pc.plugin_id}",
                ))
        return plugin_results

    async def _run_hooks(
        self,
        hook_type: str,
        plugins: List[PluginConfig],
        session_id: str,
        browser_id: str,
        page: Page,
        scope: Scope,
        mid: int | str = 0,
    ) -> str | None:
        """执行插件钩子。返回首个失败信息，全成功返回 None。"""
        if not plugins:
            return None
        results = await self._execute_plugins(
            plugins=plugins, hook_type=hook_type,
            session_id=session_id, browser_id=browser_id, page=page,
            variables=scope.current, mid=mid,
        )
        first = next((r for r in results if not r.success), None)
        return (first.error or f"{hook_type} 插件失败") if first else None

    # ═══════════════ 工具方法 ─────────────────────────────────

    @staticmethod
    def _fail(error: str, action_id: str, start: float, action_name: str = "", replaced_params: dict | None = None) -> ActionResult[Any]:
        return ActionResult(
            success=False, error=error,
            execution_time=time.time() - start,
            action_id=action_id, action_name=action_name,
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
    async def preview_action(mid: int, action_id: str, params: dict | None = None, input_vars: dict | None = None) -> Dict[str, Any]:
        from app.models.execution.action_params import BuiltinActionType
        from app.services.execution.actions.all_actions import get_action_class

        action_class = await action_registry.get_action_class_for_user(action_id)
        is_composite = action_class is not None and issubclass(action_class, CompositeActionClass)
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
                steps_preview = await ExecutionEngine._preview_steps_recursive(child_steps, mid, accumulated)
            else:
                steps_preview = []
            return {
                "action_id": action_id, "action_name": metadata.name,
                "is_composite": True, "steps_preview": steps_preview,
                "replaced_params": params, "found_params": [],
                "preview_result": {"total_steps": len(child_steps) if child_steps else 0},
                "preview_variables": dict(accumulated),
            }
        else:
            result = await ExecutionEngine._preview_action_recursive(
                action_id=action_id, params=params, mid=mid, variables=dict(accumulated), step_index=0,
            )
            return {
                "action_id": action_id, "action_name": metadata.name,
                "is_composite": False, "steps_preview": [result],
                "replaced_params": params, "found_params": [],
                "preview_result": {"single_action": True},
                "preview_variables": result.get("preview_variables", {}),
            }

    @staticmethod
    async def _preview_action_recursive(action_id, params, mid, variables, step_index=0, input_vars=None, output_vars=None):
        """
        预览辅助 — 保留以维持现有 preview 功能。
        TODO: 可后续用 Scope + Pipeline.preview() 替代。
        """
        from app.services.execution.actions.all_actions import get_action_class

        base = {
            "step_index": step_index, "action_id": action_id,
            "original_params": dict(params), "replaced_params": dict(params),
            "input_vars": dict(input_vars or {}), "output_vars": list(output_vars or []),
            "preview_variables": {},
        }

        cls = await action_registry.get_action_class_for_user(action_id)
        if cls and issubclass(cls, CompositeActionClass):
            child_steps = await action_registry.get_custom_action_steps(action_id)
            if not child_steps:
                return base
            children = await ExecutionEngine._preview_steps_recursive(child_steps, mid, dict(variables))
            child_vars = ExecutionEngine._collect_preview_vars(children)
            base["preview_variables"] = dict(child_vars)
            variables.update(child_vars)
            base["children"] = children
            return base

        if params.get("TrueBranch") is not None or params.get("FalseBranch") is not None:
            true_branch = params.get("TrueBranch", []) or []
            false_branch = params.get("FalseBranch", []) or []
            tc = await ExecutionEngine._preview_steps_recursive(true_branch, mid, dict(variables)) if true_branch else []
            fc = await ExecutionEngine._preview_steps_recursive(false_branch, mid, dict(variables)) if false_branch else []
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
            lc = await ExecutionEngine._preview_steps_recursive(loop_body, mid, loop_vars)
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
                    mid=mid, page=None, variables=dict(variables),
                    params=safe_params, output_vars=list(output_vars or []),
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
            aid = step.get("action_id", "") if isinstance(step, dict) else getattr(step, "action_id", "")
            sp = step.get("params", {}) if isinstance(step, dict) else getattr(step, "params", {})
            siv = step.get("input_vars", {}) if isinstance(step, dict) else getattr(step, "input_vars", {})
            sov = step.get("output_vars", []) if isinstance(step, dict) else getattr(step, "output_vars", [])
            r = await ExecutionEngine._preview_action_recursive(
                action_id=aid, params=sp, mid=mid, variables=dict(variables),
                step_index=idx, input_vars=siv or {}, output_vars=sov or [],
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
    async def validate_action(mid: int, action_id: str, params: dict | None = None) -> Dict[str, Any]:
        from app.models.execution.action_params import BuiltinActionType
        from app.services.execution.actions.all_actions import get_action_metadata

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
