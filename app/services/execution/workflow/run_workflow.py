"""工作流运行执行体

从 ``workflow_runner`` 拆出。把「工作流（引用一个复合操作 + 触发配置 + 目标
浏览器）」真正跑起来，并产出可观测结果（WorkflowRunRecord + SSE 进度 + 通知）。

内部按阶段拆分（原 408 行单体函数）：
1. :func:`_precheck` —— 前置校验（目标/步骤/并发/时长），失败落库并通知；
2. :func:`_open_run` —— 创建运行记录与进度上报器；
3. :func:`_execute_steps` —— 会话互斥 + 引擎执行；
4. :func:`_finalize_run` —— 汇总落库 + 终态广播 + 结束通知。

``pending_exc``（CancelledError 等非 Exception 终止信号）必须在收口后重抛，
保持协程取消语义——因此 :func:`_execute_steps` 以 in-out 字典回传该信号。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from loguru import logger
from sqlmodel import select

from app.models.database.workflow.models import (
    UserWorkflow,
    WorkflowRunRecord,
    WorkflowRunStatusEnum,
    WorkflowRunTriggerEnum,
)
from app.models.execution.request_params import WorkflowExecutionRequest
from app.models.runtime.workflow_progress import WorkflowRunProgressData
from app.services.execution.cancellation import cancellation_registry
from app.services.execution.crud_service import (
    workflow_crud_svr,
    workflow_run_crud_svr,
)
from app.services.execution.run_tasks import workflow_run_tasks
from app.services.RPA_browser.session.live_service import live_service
from app.services.membership.membership_service import MembershipService
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.execution.engine import ExecutionEngine
from app.services.execution.workflow.guards import (
    _reap_dead_run,
    _release_browser_after_run,
    list_alive_running_for_browser,
)
from app.services.execution.workflow.notify import _notify_run
from app.services.execution.workflow.precheck import (
    WorkflowRunnerError,
    _build_run_variables,
    _load_normalized_steps,
    _resolve_target_browser_id,
)
from app.services.execution.workflow_progress import WorkflowRunProgressReporter


async def _precheck(
    workflow: UserWorkflow,
    *,
    trigger_source: WorkflowRunTriggerEnum,
    browser_id: int | None,
    run_id: str | None,
    started: float,
) -> tuple[int, list[Any], dict[str, Any]]:
    """前置校验：目标浏览器 / 步骤归一化 / 输入变量 / 跨进程并发 / 时长权益。

    Returns:
        (target_browser_id, steps, variables)

    Raises:
        WorkflowRunnerError: 校验不通过（由调用方转失败落库路径）。
    """
    target_browser_id = await _resolve_target_browser_id(workflow, browser_id)
    # 步骤与动作模型一起取回：变量池要按动作声明的 input_vars（默认值/必填）组装
    steps, action_model = await _load_normalized_steps(workflow)
    variables = _build_run_variables(workflow, action_model)

    # 跨进程并发防护：同一浏览器同一时间只允许一个工作流执行（计划书 §8.6）
    # 会话内的内存互斥（entry.workflow_run_id）只在单进程内有效，多实例部署时
    # 必须靠运行记录表兜底，否则同一会话会被两个工作流共用 → 双份计费 / 统计错乱。
    running_run_id: str | None = None
    async with DatabaseSessionManager.async_session() as guard_session:
        running_run_id = (
            await guard_session.exec(
                select(WorkflowRunRecord.run_id).where(
                    WorkflowRunRecord.mid == str(workflow.mid),
                    WorkflowRunRecord.browser_id == int(target_browser_id),
                    WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING,
                )
            )
        ).first()
    if running_run_id:
        # 存活真值取自本实例的 task 注册表：该 RUNNING 记录若在本实例已无存活
        # task，即为崩溃/协程被取消遗留的僵尸记录（会话标记往往同样残留）。此时
        # 先收口再放行，否则该浏览器会被永久拦截（只能干等周期回收）。借鉴
        # Docker：启动新容器前先清理同名的僵尸容器。多实例部署时注册表不跨进程，
        # 这种方式会误收口他实例的活运行——单实例部署为前提（见模块说明）。
        if workflow_run_tasks.is_alive(running_run_id):
            raise WorkflowRunnerError(
                f"该浏览器已有工作流在执行（run={running_run_id}），"
                "请调整调度避免同浏览器并发"
            )
        await _reap_dead_run(
            running_run_id,
            mid=int(workflow.mid),
            browser_id=int(target_browser_id),
            reason="检测到僵尸运行残留（本实例已无存活执行），自动收口以放行新运行",
        )
    # 时长权益校验：仅定时任务消耗时长（计划书 §3.1）；手动运行不校验不扣费
    if trigger_source == WorkflowRunTriggerEnum.SCHEDULE:
        async with DatabaseSessionManager.async_session() as membership_session:
            allowed = await MembershipService.check_run_allowed(
                int(workflow.mid), membership_session
            )
        if not allowed:
            raise WorkflowRunnerError(
                "时长余额不足：定时任务需要时长或月卡（签到/兑换码可获取）"
            )
    return target_browser_id, steps, variables


async def _finalize_precheck_failure(
    workflow: UserWorkflow,
    *,
    exc: WorkflowRunnerError,
    browser_id: int | None,
    run_id: str | None,
    started: float,
    trigger_source: WorkflowRunTriggerEnum,
) -> dict[str, Any]:
    """前置校验失败的统一落库 + 通知 + 摘要返回"""
    mid = int(workflow.mid)
    target = browser_id if browser_id is not None else workflow.browser_id
    run = await workflow_run_crud_svr.create_running(
        workflow_id=workflow.workflow_id,
        mid=mid,
        browser_id=target if target is not None else "",
        trigger_source=trigger_source,
        total=0,
        run_id=run_id,
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
    # 配置类失败同样要通知（受 notify_on_end 开关约束），避免定时任务静默失效
    if getattr(workflow, "notify_on_end", True):
        notified = await _notify_run(
            workflow=workflow,
            mid=mid,
            browser_id=target,
            run_id=run.run_id,
            phase="failure",
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


async def _open_run(
    workflow: UserWorkflow,
    *,
    trigger_source: WorkflowRunTriggerEnum,
    target_browser_id: int,
    steps: list[Any],
    resume_step: int,
    resume_from: WorkflowRunRecord | None,
    run_id: str | None,
) -> tuple[WorkflowRunRecord, WorkflowRunProgressData, WorkflowRunProgressReporter]:
    """创建运行记录（running）+ 进度上报器（SSE 推送 + 断点检查点）"""
    inherited_success = int(resume_from.success_count or 0) if resume_from else 0
    inherited_failed = int(resume_from.failed_count or 0) if resume_from else 0
    run = await workflow_run_crud_svr.create_running(
        workflow_id=workflow.workflow_id,
        mid=int(workflow.mid),
        browser_id=target_browser_id,
        trigger_source=trigger_source,
        total=len(steps),
        run_id=run_id,
        resume_of=resume_from.run_id if resume_from else None,
        resume_count=(int(resume_from.resume_count or 0) + 1) if resume_from else 0,
        checkpoint_step=resume_step,
        success_count=inherited_success,
        failed_count=inherited_failed,
    )
    # 运行批次ID 与 步骤日志批次ID 共用，便于下钻
    progress_data = WorkflowRunProgressData(
        run_id=run.run_id,
        workflow_id=workflow.workflow_id,
        workflow_name=workflow.name,
        status=WorkflowRunStatusEnum.RUNNING.value,
        trigger_source=str(trigger_source),
        browser_id=target_browser_id,
        started_at=run.started_at,
        updated_at=run.started_at,
        total_steps=len(steps),
        completed_steps=resume_step,
        success_count=inherited_success,
        failed_count=inherited_failed,
        resume_of=run.resume_of,
        resume_count=run.resume_count,
    )
    progress_reporter = WorkflowRunProgressReporter(
        progress_data, step_offset=resume_step
    )
    return run, progress_data, progress_reporter


async def run_workflow(
    workflow: UserWorkflow,
    *,
    trigger_source: WorkflowRunTriggerEnum = WorkflowRunTriggerEnum.MANUAL,
    browser_id: int | None = None,
    auth_headers: dict[str, str] | None = None,
    run_id: str | None = None,
    resume_from: WorkflowRunRecord | None = None,
) -> dict[str, Any]:
    """执行一次工作流的**实际执行体**（手动 / 定时共用）

    通常不直接调用：入口应使用 :func:`launch_workflow_run`，它负责生成 ``run_id``、
    登记后台 task 并立即返回。本函数由该 task 执行，也可在被显式 await 的旧路径中
    直接使用（此时 ``run_id`` 为空则自行生成）。

    Args:
        workflow: 工作流记录（必须是已落库的模型）
        trigger_source: 触发来源，写入运行记录
        browser_id: 运行目标浏览器，None 时使用工作流上配置的 browser_id
        run_id: 预生成的运行标识（由 :func:`launch_workflow_run` 传入，保证 HTTP
            立即返回的 run_id 与最终落库记录一致）；None 时内部生成
        resume_from: 断点恢复来源（已中断的运行记录）。传入时从
            ``checkpoint_step`` 之后继续执行，并继承其成功/失败计数；
            循环内已处理资源由 WorkflowResourceRecord 去重（至少一次语义）。

    Returns:
        {
            run_id, status, total, success_count, failed_count,
            error, execution_id, results: [...]
        }
    """
    mid = int(workflow.mid)
    started = time.time()

    # ---------- 断点恢复起点（0 = 从头执行） ----------
    # checkpoint_step 由中断收口写入；兜底回落到 completed_steps（kill -9 时可能只
    # 有实时进度列已落库）。实际跳过长度在 steps 加载后再做上界收敛。
    resume_step = 0
    if resume_from is not None:
        resume_step = max(
            0,
            int(resume_from.checkpoint_step or resume_from.completed_steps or 0),
        )

    # ---------- 前置校验 ----------
    try:
        target_browser_id, steps, run_variables = await _precheck(
            workflow,
            trigger_source=trigger_source,
            browser_id=browser_id,
            run_id=run_id,
            started=started,
        )
        resume_step = min(resume_step, len(steps))
    except WorkflowRunnerError as exc:
        return await _finalize_precheck_failure(
            workflow,
            exc=exc,
            browser_id=browser_id,
            run_id=run_id,
            started=started,
            trigger_source=trigger_source,
        )

    # ---------- 创建运行记录（running） + 进度上报器 ----------
    run, progress_data, progress_reporter = await _open_run(
        workflow,
        trigger_source=trigger_source,
        target_browser_id=target_browser_id,
        steps=steps,
        resume_step=resume_step,
        resume_from=resume_from,
        run_id=run_id,
    )
    # 运行批次ID 与 步骤日志批次ID 共用，便于下钻
    execution_id = run.run_id

    # ---------- 开始通知（受 notify_on_start 开关约束） ----------
    if getattr(workflow, "notify_on_start", False):
        await _notify_run(
            workflow=workflow,
            mid=mid,
            browser_id=target_browser_id,
            run_id=run.run_id,
            phase="start",
        )

    error_message: str | None = None
    billed_started: float | None = None  # 计费计时起点（会话获取成功后才有值）
    results_data: list[dict[str, Any]] = []
    status = WorkflowRunStatusEnum.SUCCESS
    pending_exc: BaseException | None = None  # 非 Exception 的终止信号（如 CancelledError），收口后重抛

    try:
        # ---------- 会话与页面（定时运行允许自动拉起；统一有头，见计划书 §5.17） ----------
        entry = await live_service.get_or_create_browser_session_entry(
            mid=mid, browser_id=target_browser_id, headless=False
        )
        # 同浏览器并发防护：一个浏览器会话同一时间只允许一个工作流执行
        # （避免 workflow_run_id 相互覆盖导致双份计费/统计、熔断误伤，见计划书 §8.6）
        # 存活真值取自 task 注册表：会话标记若指向一个本实例已无存活 task 的运行，
        # 即为崩溃残留，清除后放行（否则该浏览器被永久拦截，见 §8.6 僵尸问题）。
        if entry.workflow_run_id:
            if workflow_run_tasks.is_alive(entry.workflow_run_id):
                raise WorkflowRunnerError(
                    f"该浏览器已有工作流在执行（run={entry.workflow_run_id}），"
                    "请调整调度避免同浏览器并发"
                )
            logger.warning(
                f"[WorkflowRunner] 清除残留的会话执行标记: "
                f"browser={target_browser_id}, stale_run={entry.workflow_run_id}"
            )
            live_service.end_workflow_run(mid, target_browser_id)
        # 进入执行期互斥：运行期间禁止调试类接口操作同一会话（直播为只读拉流，不受影响）
        live_service.begin_workflow_run(mid, target_browser_id, run.run_id)
        # 计费计时起点：会话成功获取后（见计划书 §5 计费接入点）
        billed_started = time.time()
        page = await entry.browser_session.get_current_page()

        req = WorkflowExecutionRequest(
            mid=mid,
            browser_id=target_browser_id,
            action_id=workflow.custom_action_id,
            workflow_id=workflow.workflow_id,
            # 动作声明默认值 ← 工作流配置值（见 _build_run_variables）
            variables=run_variables,
            input_data={},
            output_vars=[],
            execution_id=execution_id,
            auth_headers=auth_headers or {},
        )

        # 工作流粒度的日志采集总闸（三态）：True/False = 本次运行显式开/关，
        # 压制动作级 log_enabled 与项目兜底 settings.action_log_default_enabled；
        # None = 未设置，听从动作级 → 项目兜底。开启后本次运行的每个步骤（含
        # 复合/循环/分支嵌套子步骤）都落一条 ActionLogRecord（source=workflow），
        # 可在运行记录中按 execution_id（= run_id）下钻。
        results = await ExecutionEngine().execute_steps(
            req,
            steps=steps,
            session_id=str(target_browser_id),
            browser_id=target_browser_id,
            page=page,
            workflow_log_enabled=workflow.log_enabled,
            progress=progress_reporter,
            skip_steps=resume_step,
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
    except BaseException as exc:  # noqa: BLE001 - CancelledError/KeyboardInterrupt 等非 Exception 终止
        # 协程被取消（客户端断开 / 进程关停）时，`except Exception` 捕获不到，
        # 若直接向上抛出会导致 finish() 与 end_workflow_run() 都不执行：
        # 运行记录永久停留在 RUNNING，会话内存标记残留，该浏览器再也无法启动工作流。
        # 因此这里先收口为 INTERRUPTED，落库/清理完成后在函数末尾重新抛出，保持取消语义。
        pending_exc = exc
        status = WorkflowRunStatusEnum.INTERRUPTED
        error_message = f"{type(exc).__name__}: 执行被中断"
        logger.exception(
            f"[WorkflowRunner] 工作流执行被强制中断: {workflow.workflow_id}, run={run.run_id}"
        )
    finally:
        # 退出执行期互斥：无论成功 / 失败 / 异常 / 取消都必须解除，避免会话被永久锁在「执行中」
        live_service.end_workflow_run(mid, target_browser_id)
        # 运行结束且无人占用时回收浏览器会话，及时释放资源。
        # exclude_run_id 必须传：此处尚未 finish()，本次运行记录仍是 RUNNING，
        # 不排除自身会被自己的记录挡住，表现为「运行已结束但浏览器一直开着」。
        await _release_browser_after_run(
            mid, target_browser_id, exclude_run_id=run.run_id
        )

    # 协作式 / 强制停止：执行期间若收到「手动停止」请求，本次运行收口为 CANCELLED。
    # - 正常收口（SUCCESS）：步骤边界检测到停止标记 → 转 CANCELLED；
    # - 强制终止（INTERRUPTED，宽限期超时后 task.cancel()）：只要本运行确被请求过
    #   停止，即按用户停止语义记为 CANCELLED，而非笼统的「中断」。
    # 真实执行异常（FAILED）优先于停止语义，不会被稀释成 CANCELLED。
    if cancellation_registry.is_cancelled(execution_id) and status in (
        WorkflowRunStatusEnum.SUCCESS,
        WorkflowRunStatusEnum.INTERRUPTED,
    ):
        status = WorkflowRunStatusEnum.CANCELLED
        error_message = error_message or "用户手动停止"
        logger.info(f"[WorkflowRunner] 收到停止请求，运行已取消: run={run.run_id}")
    # 执行已结束，及时清理取消标记，避免键残留
    cancellation_registry.clear(execution_id)

    # ---------- 时长结算与使用统计（仅定时任务计费；手动只记统计，见计划书 §3.1） ----------
    if billed_started is not None:
        await MembershipService.settle_workflow_run(
            mid=mid,
            browser_id=target_browser_id,
            run_seconds=time.time() - billed_started,
            workflow_id=workflow.workflow_id,
            run_id=run.run_id,
            is_scheduled=trigger_source == WorkflowRunTriggerEnum.SCHEDULE,
        )

    # ---------- 汇总落库 ----------
    # 恢复运行时 results_data 只包含本次实际执行的步骤，计数需叠加旧运行的累计值
    total = len(steps)
    new_success = len([r for r in results_data if r.get("success")])
    new_failed = len([r for r in results_data if not r.get("success")])
    # 只有「未被显式容忍」的失败才决定运行结论；未知异常 / on_error=stop（含重试耗尽）
    # 打的失败不带 tolerated 标记。被 continue 跳过的失败仍计数、仍展示，只是不算结论。
    new_fatal = len(
        [
            r
            for r in results_data
            if not r.get("success") and not r.get("tolerated")
        ]
    )
    success_count = progress_data.success_count + new_success
    failed_count = progress_data.failed_count + new_failed
    if not results_data and total > resume_step and status == WorkflowRunStatusEnum.SUCCESS:
        # 引擎未返回任何结果（理论上不会）：按失败处理，避免"假成功"
        status = WorkflowRunStatusEnum.FAILED
        error_message = error_message or "执行引擎未返回任何步骤结果"

    summary = {"total": total, "success": success_count, "failed": failed_count}
    if status == WorkflowRunStatusEnum.SUCCESS and new_fatal > 0:
        status = WorkflowRunStatusEnum.FAILED
        error_message = error_message or f"存在 {new_fatal} 个失败步骤"

    duration_ms = (time.time() - started) * 1000
    # 断点：仅「非用户主动、非执行失败」的中断才可恢复（服务重启 / 关停取消 / 崩溃）
    checkpoint_step = min(progress_reporter.data.completed_steps, total)
    resumable = (
        status == WorkflowRunStatusEnum.INTERRUPTED and 0 < checkpoint_step < total
    )
    await workflow_run_crud_svr.finish(
        run_id=run.run_id,
        status=status,
        success_count=success_count,
        failed_count=failed_count,
        execution_id=execution_id,
        error_message=error_message,
        duration_ms=duration_ms,
        resumable=resumable,
        checkpoint_step=checkpoint_step,
    )
    # 推送终态帧（finish 已落库，此处只更新内存快照并广播）
    progress_data.success_count = success_count
    progress_data.failed_count = failed_count
    progress_data.completed_steps = checkpoint_step
    await progress_reporter.flush(
        status=status,
        error_message=error_message,
        duration_ms=duration_ms,
    )
    await workflow_crud_svr.update_run_state(
        id=workflow.id,
        status=status.value,
        last_run_at=datetime.now(),
    )

    # ---------- 结束通知（成功/停止/失败，受 notify_on_end 开关约束） ----------
    if getattr(workflow, "notify_on_end", True):
        if status == WorkflowRunStatusEnum.SUCCESS:
            notify_phase = "success"
        elif status == WorkflowRunStatusEnum.CANCELLED:
            notify_phase = "cancelled"
        else:
            notify_phase = "failure"
        notified = await _notify_run(
            workflow=workflow,
            mid=mid,
            browser_id=target_browser_id,
            run_id=run.run_id,
            phase=notify_phase,
            summary=summary,
            error_message=error_message,
            duration_ms=duration_ms,
        )
        if notified:
            await workflow_run_crud_svr.mark_notified(run.run_id)

    logger.info(
        f"[WorkflowRunner] 运行结束: workflow={workflow.workflow_id}, "
        f"trigger={trigger_source}, status={status.value}, "
        f"success={success_count}/{total}, cost={duration_ms:.0f}ms"
    )

    # 运行记录与内存标记均已收口，此处再重新抛出原始终止信号（如 CancelledError），
    # 保持协程取消 / 进程关停语义向上传递，避免被误当成正常返回吞掉。
    if pending_exc is not None:
        raise pending_exc

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
