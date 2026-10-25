"""MCP 动作调试服务（服务层）。

把「读动作 → 静态校验 → 试跑 → 看日志 → 落库 → 回读校验」封装成一组方法，
供 ``debug_router`` 暴露为 MCP 工具（``fastapi-mcp`` 按 OpenAPI 自动生成）。

设计约束：
- 只调用既有服务（``action_crud_svr`` / ``execution_engine`` / ``launch_workflow_run``），
  不新增执行语义、不直连 SQL；
- 执行前默认做静态校验（``validator.validate_steps``），不通过直接拦截；
- 落库后回读比对 ``action_id`` 序列，落实「JSON 与 DB 必须一致」。
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import cast

from loguru import logger

from app.mcp.config import mcp_settings
from app.mcp.logs import ActionLogDetail, ActionLogItem, summarize_run_logs
from app.mcp.models import (
    DebugActionDetail,
    DebugActionItem,
    DebugActionListData,
    DebugExecuteData,
    DebugRunData,
    DebugSaveData,
    RunLogReport,
    ValidationReport,
)
from app.mcp.requests import RecentActionLogItem, RecentActionLogsData
from app.mcp.save_payload import ActionSavePayload, ActionSavePayloadError
from app.mcp.validator import validate_steps
from app.models.database.workflow.models import (
    BuiltinActionType,
    WorkflowRunStatusEnum,
    WorkflowRunTriggerEnum,
)
from app.models.execution.action_params import (
    _ensure_action_type,
    workflow_step_adapter,
)
from app.models.execution.request_params import ActionExecutionRequest
from app.models.workflow.action_execution_models import ActionResultResponse
from app.services.RPA_browser.session.page_resolver import resolve_page
from app.services.execution.action_logger import invalidate_cache, new_execution_id
from app.services.execution.crud_service import (
    action_crud_svr,
    action_log_crud_svr,
    workflow_crud_svr,
    workflow_run_crud_svr,
)
from app.services.execution.engine import ExecutionEngine
from app.services.execution.workflow.launcher import launch_workflow_run
from app.utils.depends.admin_depends import assert_approved

#: 引擎无状态，进程内共享一个实例即可（与 execution_router 一致）
execution_engine = ExecutionEngine()


class DebugServiceError(RuntimeError):
    """调试服务的前置条件 / 参数错误（路由层转成业务错误返回）。"""


class DebugService:
    """MCP 调试能力门面。"""

    # ── 读：动作 ──────────────────────────────────────

    async def list_actions(
        self,
        mid: int,
        *,
        keyword: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> DebugActionListData:
        """按名称模糊搜索当前用户可见的自定义动作。"""
        skip = (page - 1) * per_page
        models = await action_crud_svr.list_by_user(
            mid=mid, skip=skip, limit=per_page, name=keyword
        )
        total = await action_crud_svr.count_by_user(mid=mid, name=keyword)
        return DebugActionListData(
            total=total,
            items=[
                DebugActionItem(
                    action_id=str(model.action_id),
                    name=model.name,
                    steps_count=len(model.steps or []),
                    is_enabled=model.is_enabled,
                    is_public=model.is_public,
                    updated_at=model.updated_at,
                )
                for model in models
            ],
        )

    async def get_action(self, mid: int, action_id: str) -> DebugActionDetail:
        """导出动作全量定义（DB 当前真值）。"""
        model = await self._require_action(mid, _to_int_id(action_id, "action_id"))
        return DebugActionDetail(
            action_id=str(model.action_id),
            name=model.name,
            description=model.description,
            steps=list(model.steps or []),
            step_count=len(model.steps or []),
            input_vars=list(model.input_vars or []),
            output_vars=list(model.output_vars or []),
            parameters_schema=list(model.parameters_schema or []),
            is_public=model.is_public,
            is_enabled=model.is_enabled,
            updated_at=model.updated_at,
        )

    # ── 校验 ─────────────────────────────────────────

    async def validate(
        self,
        mid: int,
        *,
        steps: object = None,
        action_id: str | None = None,
    ) -> ValidationReport:
        """静态校验：内联 steps 或已保存动作（二选一）。"""
        if steps is None and not action_id:
            raise DebugServiceError("需要提供 steps 或 action_id")
        if steps is None:
            model = await self._require_action(
                mid, _to_int_id(action_id or "", "action_id")
            )
            steps = list(model.steps or [])
        return validate_steps(steps)

    # ── 执行 ─────────────────────────────────────────

    async def execute(
        self,
        mid: int,
        *,
        browser_id: int | str,
        auth_headers: dict[str, str],
        action_id: str | None = None,
        steps: object = None,
        params: object = None,
        variables: object = None,
        output_vars: list[str] | None = None,
        page_index: int | None = None,
        force: bool = False,
    ) -> DebugExecuteData:
        """执行已保存动作或内联步骤（默认先静态校验）。"""
        if steps is None and not action_id:
            raise DebugServiceError("需要提供 action_id 或 steps")
        if steps is not None and not isinstance(steps, list):
            raise DebugServiceError("steps 必须是数组")

        validation: ValidationReport | None = None
        normalized_steps: list[object] | None = None

        if steps is not None:
            if not steps:
                raise DebugServiceError("steps 不能为空数组")
            validation = validate_steps(steps)
            if not validation.ok and not force:
                return DebugExecuteData(
                    success=False,
                    blocked_by_validation=True,
                    validation=validation,
                )
            normalized_steps = [
                workflow_step_adapter.validate_python(
                    _ensure_action_type(copy.deepcopy(step))
                )
                for step in steps
            ]
        elif action_id and not force and _is_saved_action(action_id):
            model = await self._require_action(
                mid, _to_int_id(action_id, "action_id")
            )
            validation = validate_steps(list(model.steps or []))
            if not validation.ok:
                return DebugExecuteData(
                    success=False,
                    blocked_by_validation=True,
                    validation=validation,
                )

        page = await resolve_page(mid, browser_id, page_index)
        execution_id = new_execution_id()
        request = ActionExecutionRequest(
            mid=mid,
            browser_id=browser_id,
            action_id=action_id or "inline",
            params=cast("dict", params) if params is not None else None,
            variables=cast("dict", variables) if variables is not None else {},
            input_data=cast("dict", variables) if variables is not None else {},
            output_vars=output_vars or [],
            page_index=page_index,
            execution_id=execution_id,
            auth_headers=auth_headers,
        )

        if normalized_steps is not None:
            results = await execution_engine.execute_steps(
                request,
                steps=cast("list", normalized_steps),
                session_id=str(browser_id),
                browser_id=str(browser_id),
                page=page,
            )
        else:
            results = [
                await execution_engine.execute_action(
                    request,
                    session_id=str(browser_id),
                    browser_id=str(browser_id),
                    page=page,
                )
            ]

        return DebugExecuteData(
            success=all(result.success for result in results),
            execution_id=execution_id,
            results=[self._to_response(result) for result in results],
            validation=validation,
        )

    # ── 落库 ─────────────────────────────────────────

    async def save(
        self,
        mid: int,
        *,
        content: object,
        action_id: str | None = None,
        dry_run: bool = False,
    ) -> DebugSaveData:
        """落库动作定义（同名 upsert）并回读比对。"""
        try:
            payload = ActionSavePayload.from_content(content)
        except ActionSavePayloadError as exc:
            raise DebugServiceError(str(exc)) from exc

        report = validate_steps(payload.steps)
        if not report.ok:
            raise DebugServiceError(
                "静态校验未通过，拒绝落库："
                + "; ".join(f"{issue.path} {issue.message}" for issue in report.issues[:5])
            )

        existing = None
        if action_id is not None:
            existing = await self._require_action(
                mid, _to_int_id(action_id, "action_id")
            )
        else:
            matched = await self._find_action_id_by_name(mid, payload.name)
            if matched is not None:
                existing = await self._require_action(mid, matched)

        mode = "update" if existing is not None else "import"
        if dry_run:
            return DebugSaveData(
                mode=mode,
                action_id=str(existing.action_id) if existing is not None else None,
                name=payload.name,
                step_count=len(payload.steps),
                action_ids=payload.step_action_ids(),
                dry_run=True,
                validation=report,
            )

        if existing is not None:
            model = await action_crud_svr.update(
                id=int(existing.id or 0),
                name=payload.name,
                description=payload.description,
                parameters_schema=cast("list", payload.parameters_schema),
                steps=cast("list", payload.steps),
                tags=payload.tags,
                input_vars=cast("list", payload.input_vars),
                output_vars=payload.output_vars,
                icon_series=payload.icon_series,
                icon_id=payload.icon_id,
                timeout=payload.timeout,
                retry_on_error=payload.retry_on_error,
                retry_times=payload.retry_times,
                retry_delay=payload.retry_delay,
                log_enabled=payload.log_enabled,
                log_debug=payload.log_debug,
                log_record_params=payload.log_record_params,
                log_record_result=payload.log_record_result,
                log_record_variables=payload.log_record_variables,
                log_only_on_error=payload.log_only_on_error,
                log_max_payload_length=payload.log_max_payload_length,
                log_retention_days=payload.log_retention_days,
            )
            if model is None:
                raise DebugServiceError(f"动作 {existing.action_id} 更新失败")
        else:
            model = await action_crud_svr.create(
                mid=mid,
                name=payload.name,
                action_type=BuiltinActionType.COMPOSITE,
                description=payload.description,
                parameters_schema=cast("list", payload.parameters_schema),
                steps=cast("list", payload.steps),
                tags=payload.tags,
                input_vars=cast("list", payload.input_vars),
                output_vars=payload.output_vars,
                is_public=False,  # 调试通道不发布，公开仍需走前端 publish 审批
                icon_series=payload.icon_series,
                icon_id=payload.icon_id,
                timeout=payload.timeout,
                retry_on_error=payload.retry_on_error,
                retry_times=payload.retry_times,
                retry_delay=payload.retry_delay,
                log_enabled=payload.log_enabled,
                log_debug=payload.log_debug,
                log_record_params=payload.log_record_params,
                log_record_result=payload.log_record_result,
                log_record_variables=payload.log_record_variables,
                log_only_on_error=payload.log_only_on_error,
                log_max_payload_length=payload.log_max_payload_length,
                log_retention_days=payload.log_retention_days,
            )

        saved_action_id = int(model.action_id)
        invalidate_cache(saved_action_id)

        readback = await action_crud_svr.get_by_action_id(saved_action_id)
        db_action_ids = [
            str(step.get("action_id", ""))
            for step in (readback.steps if readback else [])
            if isinstance(step, dict)
        ]
        expected_ids = payload.step_action_ids()
        return DebugSaveData(
            mode=mode,
            action_id=str(saved_action_id),
            name=payload.name,
            step_count=len(payload.steps),
            action_ids=expected_ids,
            verified=db_action_ids == expected_ids,
            db_action_ids=db_action_ids,
            validation=report,
        )

    # ── 工作流 ───────────────────────────────────────

    async def run_workflow_and_wait(
        self,
        mid: int,
        *,
        workflow_id: str,
        browser_id: int | str,
        auth_headers: dict[str, str],
        timeout_seconds: int,
    ) -> DebugRunData:
        """异步启动工作流并等待终态（服务端轮询运行记录）。"""
        model = await workflow_crud_svr.get_by_id(
            _to_int_id(workflow_id, "workflow_id")
        )
        if not model or str(model.mid) != str(mid):
            raise DebugServiceError("工作流不存在或无权限")
        if not model.custom_action_id:
            raise DebugServiceError("工作流未关联任何动作")
        await assert_approved("workflow", model.workflow_id, "execute")

        run_id = await launch_workflow_run(
            model,
            trigger_source=WorkflowRunTriggerEnum.MANUAL,
            browser_id=int(browser_id),
            auth_headers=auth_headers,
        )

        started = time.monotonic()
        deadline = started + timeout_seconds
        run = None
        while True:
            run = await workflow_run_crud_svr.get_by_run_id(run_id, mid)
            if run is not None and run.status != WorkflowRunStatusEnum.RUNNING:
                break
            if time.monotonic() >= deadline:
                logger.warning(f"[MCP] 等待运行 {run_id} 超时（{timeout_seconds}s）")
                break
            await asyncio.sleep(mcp_settings.poll_interval)

        waited = round(time.monotonic() - started, 1)
        if run is None:
            raise DebugServiceError(f"运行记录 {run_id} 不存在（启动可能失败）")

        finished = run.status != WorkflowRunStatusEnum.RUNNING
        return DebugRunData(
            run_id=run_id,
            workflow_id=str(model.workflow_id),
            status=str(run.status),
            finished=finished,
            execution_id=run.execution_id or "",
            total=run.total,
            success_count=run.success_count,
            failed_count=run.failed_count,
            error_message=run.error_message,
            duration_ms=run.duration_ms,
            waited_seconds=waited,
        )

    # ── 日志 ─────────────────────────────────────────

    async def get_run_logs(
        self,
        mid: int,
        *,
        run_id: str | None = None,
        execution_id: str | None = None,
        max_items: int = 200,
        only_failed: bool = False,
        detail_limit: int = 10,
    ) -> RunLogReport:
        """按运行 / 执行批次拉步骤链路，并把失败项映射为可改的 steps 路径。"""
        resolved_execution_id = execution_id or ""
        if run_id and not resolved_execution_id:
            run = await workflow_run_crud_svr.get_by_run_id(run_id, mid)
            if run is None:
                raise DebugServiceError(f"运行记录不存在: {run_id}")
            resolved_execution_id = run.execution_id or ""
            if not resolved_execution_id:
                raise DebugServiceError(
                    f"运行 {run_id} 尚无 execution_id（可能刚启动或未开启步骤日志）"
                )
        if not resolved_execution_id:
            raise DebugServiceError("需要提供 run_id 或 execution_id")

        total = await action_log_crud_svr.count(mid, execution_id=resolved_execution_id)
        if only_failed:
            rows = await action_log_crud_svr.list(
                mid,
                skip=0,
                limit=max_items,
                order_desc=False,
                execution_id=resolved_execution_id,
                success=False,
            )
        else:
            rows = await action_log_crud_svr.list(
                mid,
                skip=0,
                limit=max_items,
                order_desc=False,
                execution_id=resolved_execution_id,
            )
        items = [ActionLogItem.model_validate(row, from_attributes=True) for row in rows]

        details: dict[str, ActionLogDetail] = {}
        for item in [entry for entry in items if not entry.success][:detail_limit]:
            if not item.log_id:
                continue
            row = await action_log_crud_svr.get_by_log_id(mid, item.log_id)
            if row is None:
                continue
            details[item.log_id] = ActionLogDetail.model_validate(row, from_attributes=True)

        return summarize_run_logs(
            resolved_execution_id,
            items,
            total,
            details,
            truncated=total > len(items),
            max_chars=mcp_settings.payload_max_chars,
        )

    async def recent_action_logs(
        self, mid: int, *, action_id: str, limit: int = 10
    ) -> RecentActionLogsData:
        """查某动作最近的执行日志（不挂工作流时的快速反馈）。"""
        total = await action_log_crud_svr.count(mid, action_id=str(action_id))
        rows = await action_log_crud_svr.list(
            mid, skip=0, limit=limit, order_desc=True, action_id=str(action_id)
        )
        return RecentActionLogsData(
            total=total,
            items=[
                RecentActionLogItem(
                    log_id=row.log_id,
                    execution_id=row.execution_id,
                    action_name=row.action_name,
                    status=str(row.status),
                    success=row.success,
                    error_message=row.error_message,
                    execution_time=row.execution_time,
                )
                for row in rows
            ],
        )

    # ── 内部工具 ──────────────────────────────────────

    async def _require_action(self, mid: int, action_id: int):
        """取动作并校验可见性（本人或已公开）。"""
        model = await action_crud_svr.get_by_action_id(int(action_id))
        if not model:
            raise DebugServiceError(f"动作不存在: {action_id}")
        if str(model.mid) != str(mid) and not model.is_public:
            raise DebugServiceError(f"动作不存在或无权限: {action_id}")
        return model

    async def _find_action_id_by_name(self, mid: int, name: str) -> int | None:
        """按名称精确匹配已有动作（upsert 语义）。"""
        models = await action_crud_svr.list_by_user(mid=mid, skip=0, limit=50, name=name)
        for model in models:
            if model.name == name:
                return int(model.action_id)
        return None

    @staticmethod
    def _to_response(result: object) -> ActionResultResponse:
        """引擎结果 → 路由响应模型。"""
        return ActionResultResponse(
            success=bool(getattr(result, "success", False)),
            data=getattr(result, "data", None),
            error=getattr(result, "error", None),
            execution_time=float(getattr(result, "execution_time", 0.0) or 0.0),
            action_id=getattr(result, "action_id", ""),
            action_name=getattr(result, "action_name", ""),
            variables=getattr(result, "variables", {}) or {},
            replaced_params=getattr(result, "replaced_params", {}) or {},
        )


def _is_saved_action(action_id: str) -> bool:
    """复合动作 ID（数字串）才需要读 DB 做校验；内置动作名跳过。"""
    return action_id.strip().isdigit()


def _to_int_id(value: str, field: str) -> int:
    """把字符串 ID 转 int（雪花 ID 用字符串传输，避免前端精度丢失）。"""
    raw = value.strip()
    if not raw.isdigit():
        raise DebugServiceError(f"{field} 必须是数字字符串: {value!r}")
    return int(raw)


debug_service = DebugService()

__all__ = ["DebugService", "DebugServiceError", "debug_service", "execution_engine"]
