"""
工作流执行器（调度外壳的运行时）

职责：把「工作流（引用一个复合操作 + 触发配置 + 目标浏览器）」真正跑起来，
并产出可观测结果：
    - WorkflowRunRecord：一次运行的聚合记录（不依赖 action 的 log_enabled）
    - ActionLogRecord：步骤级明细（由 ExecutionEngine.execute_steps 自动写入，
      source=workflow + workflow_id，通过 execution_id 与运行记录 1:1 关联）
    - 失败推送：复用 NotificationConfig + push_msg（发布到 message-service）

设计约束：
    1. 工作流不改写引用的 action，只按顺序执行其 steps（action 是多对一共享资产）。
    2. 定时运行允许自动拉起浏览器会话（headless），无需人工先开会话。
    3. 本模块所有异常都被收敛为「运行失败」，绝不向上抛出打断调度线程。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List

from loguru import logger

from app.models.database.workflow.models import (
    UserWorkflow,
    WorkflowRunStatusEnum,
    WorkflowRunTriggerEnum,
)
from app.models.execution.request_params import WorkflowExecutionRequest
from app.services.execution.crud_service import (
    action_crud_svr,
    workflow_crud_svr,
    workflow_run_crud_svr,
)


class WorkflowRunnerError(Exception):
    """工作流运行前置校验失败（配置类问题，非执行异常）"""


async def _resolve_target_browser_id(
    workflow: UserWorkflow, browser_id: int | None
) -> int:
    """解析执行目标浏览器：手动运行优先用入参，否则用工作流上配置的"""
    target = browser_id if browser_id is not None else workflow.browser_id
    if target is None:
        raise WorkflowRunnerError("工作流未配置执行目标浏览器")
    return int(target)


async def _load_normalized_steps(workflow: UserWorkflow) -> List[Any]:
    """加载并归一化被引用 action 的步骤"""
    if not workflow.custom_action_id:
        raise WorkflowRunnerError("工作流未关联任何动作")

    action_model = await action_crud_svr.get_by_action_id(workflow.custom_action_id)
    if not action_model:
        raise WorkflowRunnerError(f"关联的动作不存在: {workflow.custom_action_id}")
    if not getattr(action_model, "is_enabled", True):
        raise WorkflowRunnerError(f"关联的动作已被禁用: {workflow.custom_action_id}")

    from app.models.execution.action_params import (
        _ensure_action_type,
        workflow_step_adapter,
    )

    normalized: List[Any] = []
    for step in action_model.steps or []:
        if isinstance(step, dict):
            step = workflow_step_adapter.validate_python(_ensure_action_type(step))
        normalized.append(step)
    if not normalized:
        raise WorkflowRunnerError(f"关联的动作没有任何步骤: {workflow.custom_action_id}")
    return normalized


async def _notify_failure(
    workflow: UserWorkflow,
    mid: int,
    browser_id: int | None,
    run_id: str,
    summary: Dict[str, Any],
    error_message: str | None,
) -> bool:
    """失败推送（未配置通知渠道时静默跳过，推送异常只告警）

    返回 True 表示推送已发出（用于标记 run 记录 notified）。
    """
    try:
        from app.services.RPA_browser.browser import BrowserService
        from app.services.message import push_msg
        from app.utils.depends.session_manager import DatabaseSessionManager

        async with DatabaseSessionManager.async_session() as session:
            config = await BrowserService(mid).get_notification_config(session, browser_id)
        if not config:
            logger.info(f"[WorkflowRunner] 未配置推送通知，跳过失败通知: run={run_id}")
            return False

        detail = error_message or (
            f"成功 {summary.get('success', 0)} / 失败 {summary.get('failed', 0)}"
            f"（共 {summary.get('total', 0)} 步）"
        )
        content = (
            f"工作流：{workflow.name}（{workflow.workflow_id}）\n"
            f"触发方式：{workflow.trigger_type}\n"
            f"目标浏览器：{browser_id}\n"
            f"运行记录：{run_id}\n"
            f"失败详情：{detail}"
        )
        await push_msg.send(
            title=f"工作流执行失败: {workflow.name}",
            content=content,
            conf=config,
            subject=push_msg.PushSubject.FAILURE,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 通知失败绝不影响业务
        logger.warning(f"[WorkflowRunner] 发送失败通知异常: run={run_id}, error={exc}")
        return False


async def run_workflow(
    workflow: UserWorkflow,
    *,
    trigger_source: WorkflowRunTriggerEnum = WorkflowRunTriggerEnum.MANUAL,
    browser_id: int | None = None,
    auth_headers: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """执行一次工作流（手动 / 定时共用入口）

    Args:
        workflow: 工作流记录（必须是已落库的模型）
        trigger_source: 触发来源，写入运行记录
        browser_id: 运行目标浏览器，None 时使用工作流上配置的 browser_id

    Returns:
        {
            run_id, status, total, success_count, failed_count,
            error, execution_id, results: [...]
        }
    """
    from app.services.execution.engine import ExecutionEngine
    from app.services.RPA_browser.session.live_service import live_service

    mid = int(workflow.mid)
    started = time.time()

    # ---------- 前置校验 ----------
    try:
        target_browser_id = await _resolve_target_browser_id(workflow, browser_id)
        steps = await _load_normalized_steps(workflow)
    except WorkflowRunnerError as exc:
        target = browser_id if browser_id is not None else workflow.browser_id
        run = await workflow_run_crud_svr.create_running(
            workflow_id=workflow.workflow_id,
            mid=mid,
            browser_id=target if target is not None else "",
            trigger_source=trigger_source,
            total=0,
        )
        await workflow_run_crud_svr.finish(
            run_id=run.run_id,
            status=WorkflowRunStatusEnum.FAILED,
            error_message=str(exc),
            duration_ms=(time.time() - started) * 1000,
        )
        await workflow_crud_svr.update_run_state(
            id=workflow.id,
            status=WorkflowRunStatusEnum.FAILED.value,
            last_run_at=datetime.now(),
        )
        # 配置类失败同样要通知，避免定时任务静默失效
        notified = await _notify_failure(
            workflow=workflow,
            mid=mid,
            browser_id=target,
            run_id=run.run_id,
            summary={"total": 0, "success": 0, "failed": 0},
            error_message=str(exc),
        )
        if notified:
            await workflow_run_crud_svr.mark_notified(run.run_id)
        logger.warning(f"[WorkflowRunner] 前置校验失败: {workflow.workflow_id}, {exc}")
        return {
            "run_id": run.run_id,
            "status": WorkflowRunStatusEnum.FAILED.value,
            "total": 0,
            "success_count": 0,
            "failed_count": 0,
            "error": str(exc),
            "execution_id": "",
            "results": [],
        }

    # ---------- 创建运行记录（running） ----------
    run = await workflow_run_crud_svr.create_running(
        workflow_id=workflow.workflow_id,
        mid=mid,
        browser_id=target_browser_id,
        trigger_source=trigger_source,
        total=len(steps),
    )
    # 运行批次ID 与 步骤日志批次ID 共用，便于下钻
    execution_id = run.run_id

    error_message: str | None = None
    results_data: List[Dict[str, Any]] = []
    status = WorkflowRunStatusEnum.SUCCESS

    try:
        # ---------- 会话与页面（定时运行允许自动拉起） ----------
        entry = await live_service.get_or_create_browser_session_entry(
            mid=mid, browser_id=target_browser_id, headless=True
        )
        page = await entry.browser_session.get_current_page()

        plugins = await workflow_crud_svr.get_enabled_plugins(workflow.workflow_id)

        req = WorkflowExecutionRequest(
            mid=mid,
            browser_id=target_browser_id,
            action_id=workflow.custom_action_id,
            workflow_id=workflow.workflow_id,
            variables={},
            input_data={},
            output_vars=[],
            execution_id=execution_id,
            auth_headers=auth_headers or {},
        )

        results = await ExecutionEngine().execute_steps(
            req,
            steps=steps,
            session_id=str(target_browser_id),
            browser_id=str(target_browser_id),
            page=page,
            plugins=plugins,
        )

        results_data = [
            {
                "success": r.success,
                "data": r.data,
                "error": r.error,
                "execution_time": r.execution_time,
                "action_id": r.action_id,
                "action_name": r.action_name,
            }
            for r in results
        ]
    except Exception as exc:  # noqa: BLE001 - 收敛为运行失败
        status = WorkflowRunStatusEnum.FAILED
        error_message = f"{type(exc).__name__}: {exc}"
        logger.exception(
            f"[WorkflowRunner] 工作流执行异常: {workflow.workflow_id}, run={run.run_id}"
        )

    # ---------- 汇总落库 ----------
    total = len(results_data)
    success_count = len([r for r in results_data if r.get("success")])
    failed_count = len([r for r in results_data if not r.get("success")])
    if total == 0 and status == WorkflowRunStatusEnum.SUCCESS:
        # 引擎未返回任何结果（理论上不会）：按失败处理，避免"假成功"
        status = WorkflowRunStatusEnum.FAILED
        error_message = error_message or "执行引擎未返回任何步骤结果"

    summary = {"total": total, "success": success_count, "failed": failed_count}
    if status == WorkflowRunStatusEnum.SUCCESS and failed_count > 0:
        status = WorkflowRunStatusEnum.FAILED
        error_message = error_message or f"存在 {failed_count} 个失败步骤"

    duration_ms = (time.time() - started) * 1000
    await workflow_run_crud_svr.finish(
        run_id=run.run_id,
        status=status,
        success_count=success_count,
        failed_count=failed_count,
        execution_id=execution_id,
        error_message=error_message,
        duration_ms=duration_ms,
    )
    await workflow_crud_svr.update_run_state(
        id=workflow.id,
        status=status.value,
        last_run_at=datetime.now(),
    )

    # ---------- 失败通知 ----------
    if status == WorkflowRunStatusEnum.FAILED:
        notified = await _notify_failure(
            workflow=workflow,
            mid=mid,
            browser_id=target_browser_id,
            run_id=run.run_id,
            summary=summary,
            error_message=error_message,
        )
        if notified:
            await workflow_run_crud_svr.mark_notified(run.run_id)

    logger.info(
        f"[WorkflowRunner] 运行结束: workflow={workflow.workflow_id}, "
        f"trigger={trigger_source}, status={status.value}, "
        f"success={success_count}/{total}, cost={duration_ms:.0f}ms"
    )

    return {
        "run_id": run.run_id,
        "status": status.value,
        "total": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "error": error_message,
        "execution_id": execution_id,
        "results": results_data,
    }
