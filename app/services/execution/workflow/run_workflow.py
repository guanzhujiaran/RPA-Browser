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

from app.config import settings
from app.models.database.workflow.models import (
    UserWorkflow,
    WorkflowRunRecord,
    WorkflowRunStatusEnum,
    WorkflowRunTriggerEnum,
)
from app.models.execution.request_params import WorkflowExecutionRequest
from app.models.execution.resource_stats import RunResourceStats
from app.models.runtime.workflow_progress import (
    LoopRunSummary,
    WorkflowRunProgressData,
    render_loop_summaries,
)
from app.services.execution.cancellation import cancellation_registry
from app.services.execution.crud_service import workflow_run_crud_svr
from app.services.execution.crud_service.resource_record_crud import (
    ResourceRecordService,
)
from app.services.execution.run_events import WorkflowRunSummary
from app.services.execution.run_tasks import workflow_run_tasks
from app.services.RPA_browser.session.live_service import live_service
from app.services.RPA_browser.session.page_registry import page_registry
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


def _resolve_run_counts(
    *, results_data: list[dict[str, Any]], progress_success: int, progress_failed: int
) -> tuple[int, int, int]:
    """汇总运行结束时的步骤计数，返回 (成功数, 失败数, 致命失败数)。

    **不要用 results_data 的条数去加进度计数**：两边统计的是同一批顶层步骤，
    相加会翻倍（历史 run 记录 `total=18 / success=36` 即由此而来，通知文案也
    跟着写成「成功 36 / 失败 0（共 18 步）」）。

    - 成功 / 失败：以进度上报器为准（它是每个顶层步骤的唯一终态来源，
      断点恢复时已带上继承值）；
    - 致命失败：只看本次返回里「未被 on_error=continue 标记 tolerated」的失败，
      用于决定运行结论（重试耗尽 / on_error=stop / 未捕获异常都属于这一类）。
      进度计数里混着被容忍的失败，不能直接拿来判结论。
    """
    fatal = len(
        [
            r
            for r in results_data
            if not r.get("success") and not r.get("tolerated")
        ]
    )
    return progress_success, progress_failed, fatal


def _build_resource_failure_message(
    stats: RunResourceStats, threshold: int
) -> str:
    """资源级失败文案（运行结论的主判据）

    步骤全绿但资源失败，是业务工作流最常见的「假成功」；文案必须把
    「成功/失败/跳过」三个口径都写出来，并说明失败会被下一轮重试。
    """
    return (
        f"{stats.describe()}，其中 {stats.failed} 条资源处理失败"
        f"（阈值 {threshold}），判定为失败；失败资源下一轮会被重新捞起重试"
    )


async def _collect_resource_stats(run_id: str) -> RunResourceStats:
    """读取本次运行的资源记录统计

    统计失败绝不能让收口流程异常（否则运行永远停在 RUNNING），
    查询异常时退化为空统计，运行结论回到步骤级判定。
    """
    try:
        return await ResourceRecordService.count_by_execution(run_id)
    except Exception as exc:  # noqa: BLE001 - 统计失败降级为无资源数据
        logger.warning(
            f"[WorkflowRunner] 资源统计失败，退回步骤级判定: run={run_id}, {exc}"
        )
        return RunResourceStats()


def _should_trip_tolerated_breaker(
    *,
    tolerated_count: int,
    threshold: int,
    signature_count: int,
    loop_summaries: list[LoopRunSummary],
    max_ratio: float,
) -> tuple[bool, float]:
    """容错熔断是否成立（返回 (是否判失败, 循环体内失败占比)）。

    旧口径只看「容忍失败绝对次数」：批量循环里同一处降级（如 LLM 额度耗尽→
    回抄评论）会在每条数据上重复一次，200 条数据就是 200 次，必然误判
    （2026-11-06 run 4e7b37d：62 成功 / 13 失败被判成运行失败）。

    现在的口径（两者取其一即熔断）：
    1. **不同错误类型数**达阈值——说明是多处能力同时坏了，必须提醒；
    2. **循环体内失败占比**超 `max_ratio`——说明是「大面积失败」，仍然要拦。
    没有循环的流程（body_total=0）保持旧语义（达阈值即判失败），避免放宽过度。
    """
    if threshold <= 0 or tolerated_count < threshold:
        return False, 0.0

    body_total = sum(s.body_success + s.body_failed for s in loop_summaries)
    if body_total <= 0:
        # 无循环聚合：沿用旧行为（占比按 100% 计），不因缺少统计而放宽
        return True, 1.0

    failure_ratio = tolerated_count / body_total
    if max_ratio > 0 and failure_ratio < max_ratio:
        # 占比不高时，只有「问题种类足够多」才值得判失败
        if signature_count < threshold:
            return False, failure_ratio
    return True, failure_ratio


def _build_tolerated_breaker_message(
    *,
    success_count: int,
    total: int,
    tolerated_count: int,
    threshold: int,
    loop_summaries: list[LoopRunSummary],
    samples: list[str],
    failure_ratio: float | None = None,
    signature_count: int | None = None,
) -> str:
    """容错熔断的失败原因文案（步骤级 + 循环级全量上下文，见计划书 §D4）。

    熔断的主场景是批量循环容错——顶层可能只有 1 个步骤，只写「N 次失败被跳过」
    用户看不出整体干成了多少，因此带上顶层步骤成败与循环聚合。
    措辞是「次」不是「步」：`tolerated_count` 含循环体内失败，与只数顶层步骤的
    `success_count/total` 不同口径，写「步」会自相矛盾。
    """
    loop_part = render_loop_summaries(loop_summaries)
    if loop_part:
        loop_part = f"；{loop_part}"
    # 判失败依据（占比 / 不同错误类型数）：只在调用方传入时展示，
    # 保持旧调用（测试）文案不变
    reason_parts: list[str] = []
    if failure_ratio is not None:
        reason_parts.append(f"循环体内失败占比 {failure_ratio * 100:.1f}%")
    if signature_count is not None:
        reason_parts.append(f"不同错误 {signature_count} 类")
    reason = f"（{'，'.join(reason_parts)}）" if reason_parts else ""
    hint = f"；示例: {'；'.join(samples[:2])}" if samples else ""
    return (
        f"步骤成功 {success_count}/{total}，"
        f"{tolerated_count} 次失败被容错跳过（熔断阈值 {threshold}）"
        f"{loop_part}{reason}，判定为失败{hint}"
    )


def _build_loop_abort_message(
    *, loop_summaries: list[LoopRunSummary], abort_count: int = 1
) -> str:
    """循环中断失败的运行级文案。

    与容错熔断的区别：熔断是「失败次数达到阈值」，而循环中断是**结构性失败**
    ——某一层循环因体内步骤失败被提前打断，剩余数据根本没跑；这种情况不看失败
    占比，直接判失败，文案要说清是哪一层循环、跑到第几轮。
    """
    aborted = [s for s in loop_summaries if s.aborted_by_failure]
    detail = "、".join(
        f"「{s.name or '未命名循环'}」执行 {s.rounds}"
        + (f"/{s.rounds_planned}" if s.rounds_planned else "")
        + " 轮后中断"
        for s in aborted[:3]
    )
    if not detail:
        detail = "循环因步骤失败被中断"
    return f"{abort_count} 层循环中断（{detail}），判定为失败"


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
    existing = (
        await workflow_run_crud_svr.get_by_run_id(run_id) if run_id else None
    )
    if existing is not None and existing.status == WorkflowRunStatusEnum.QUEUED:
        # 排队记录已存在：直接落失败终态，不能重复插入同一 run_id
        await workflow_run_crud_svr.finish_queued(
            run_id,
            status=WorkflowRunStatusEnum.FAILED,
            error_message=str(exc),
        )
        run = existing
    else:
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
    # 工作流卡片快照（last_run_status / last_run_at）由终态事件订阅者刷新
    # （见 docs/工作流运行状态机计划书.md §7），这里不再直接写，避免两处口径。
    # 配置类失败同样要通知（按「结束」时机的通道开关），避免定时任务静默失效；
    # 开关全关时 _notify_run 直接返回 False，不会写 notified 标记
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
    queued = None
    if run_id is not None:
        queued = await workflow_run_crud_svr.get_by_run_id(run_id)
    if queued is not None and queued.status == WorkflowRunStatusEnum.CANCELLED:
        # 停止工作流会把排队记录直接置 CANCELLED：绝不能在这里新建 RUNNING 记录
        # 把已取消的运行「复活」（见 docs/工作流停止计划书.md §3.3）
        raise WorkflowRunnerError(f"运行已被取消: run={run_id}")
    if queued is not None and queued.status == WorkflowRunStatusEnum.QUEUED:
        # 排队记录已存在：直接激活为 RUNNING（保持 run_id 与前端看到的一致）
        run = await workflow_run_crud_svr.mark_running(
            str(run_id),
            total=len(steps),
            resume_of=resume_from.run_id if resume_from else None,
            resume_count=(int(resume_from.resume_count or 0) + 1)
            if resume_from
            else 0,
            checkpoint_step=resume_step,
            success_count=inherited_success,
            failed_count=inherited_failed,
        )
        if run is None:
            raise WorkflowRunnerError(
                f"排队记录已失效（可能被取消或超时）: run={run_id}"
            )
    else:
        run = await workflow_run_crud_svr.create_running(
            workflow_id=workflow.workflow_id,
            mid=int(workflow.mid),
            browser_id=target_browser_id,
            trigger_source=trigger_source,
            total=len(steps),
            run_id=run_id,
            resume_of=resume_from.run_id if resume_from else None,
            resume_count=(int(resume_from.resume_count or 0) + 1)
            if resume_from
            else 0,
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

    # ---------- 目标浏览器（排队需要队列键，提前解析） ----------
    try:
        target_browser_id = await _resolve_target_browser_id(workflow, browser_id)
    except WorkflowRunnerError as exc:
        return await _finalize_precheck_failure(
            workflow,
            exc=exc,
            browser_id=browser_id,
            run_id=run_id,
            started=started,
            trigger_source=trigger_source,
        )

    # 同一浏览器上多个工作流**并行**执行（见 docs/多页面并行执行计划书.md §3），
    # 不再等待前序运行；页面资源由 open_page 的容量等待负责。
    run_id = run_id or workflow_run_crud_svr.new_run_id()

    # ---------- 前置校验（排队已保证同浏览器串行） ----------
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

    # ---------- 创建/激活运行记录 + 进度上报器 ----------
    try:
        run, progress_data, progress_reporter = await _open_run(
            workflow,
            trigger_source=trigger_source,
            target_browser_id=target_browser_id,
            steps=steps,
            resume_step=resume_step,
            resume_from=resume_from,
            run_id=run_id,
        )
    except Exception:
        # 记录激活失败：task 即将结束，执行权由 done_callback 释放（避免永久占用）
        raise

    # 运行批次ID 与 步骤日志批次ID 共用，便于下钻
    execution_id = run.run_id

    # ---------- 开始通知（按「开始」时机的通道开关：站内/站外各自独立） ----------
    # 开关全关时 _notify_run 内部直接返回，无需在此预先判断（见 notify_flags）
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
        # 同一浏览器上**允许多个工作流并行**（见 docs/多页面并行执行计划书.md §3），
        # 因此这里不再拒绝「已有运行」，只清理会话标记里已无存活 task 的僵尸项，
        # 避免崩溃残留让调试类接口被永久拒绝。
        stale_runs = [
            rid
            for rid in list(entry.active_workflow_runs)
            if not workflow_run_tasks.is_alive(rid)
        ]
        for stale in stale_runs:
            logger.warning(
                f"[WorkflowRunner] 清除残留的会话执行标记: "
                f"browser={target_browser_id}, stale_run={stale}"
            )
            live_service.end_workflow_run(mid, target_browser_id, stale)
        # 进入执行期互斥：有工作流运行时禁止调试类接口操作该会话（直播只读，不受影响）
        live_service.begin_workflow_run(mid, target_browser_id, run.run_id)
        # 计费计时起点：会话成功获取后（见计划书 §5 计费接入点）
        billed_started = time.time()
        page = await entry.browser_session.get_current_page()
        # 默认页面绑定为别名 main：步骤可用 params.page 引用其它页
        page_registry.bind_main((mid, target_browser_id), page)

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
        # log_debug（调试模式，用户级开关）优先于总闸：开启后强制全量采集
        # （入参/结果/变量全记、成功也记、payload 不截断，见调度外壳计划书 §9.8）。
        results = await ExecutionEngine().execute_steps(
            req,
            steps=steps,
            session_id=str(target_browser_id),
            browser_id=target_browser_id,
            page=page,
            workflow_log_enabled=workflow.log_enabled,
            workflow_log_debug=workflow.log_debug,
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
                # tolerated 必须一起带出来：下面统计「致命失败」时要靠它区分
                # on_error=continue 显式容忍的失败，缺了会把容错失败当致命失败。
                "tolerated": bool(r.tolerated),
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
        # 退出执行期互斥：无论成功 / 失败 / 异常 / 取消都必须解除本次标记
        # （集合语义，其它并行运行不受影响）
        # ⚠️ 必须容错：这里抛异常会让整个 finally 中断，跳过下面的终态落库，
        # 运行记录永久停在 RUNNING，最终被兜底收口成「未正常收口 → 中断」。
        try:
            live_service.end_workflow_run(mid, target_browser_id, run.run_id)
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响收口
            logger.warning(
                f"[WorkflowRunner] 解除执行期标记失败: run={run.run_id}, {exc}"
            )
        # 执行权在 task 结束回调里释放（此处本次运行尚未写终态，提前释放会让下一位
        # 的并发检查误判为「同浏览器已有工作流在执行」）；这里只判断是否还要保留浏览器
        # 运行结束：只关闭本次运行自己打开的页面（main 与用户手动开的页保留），
        # 浏览器交给空闲生命周期回收（见多页面计划书 §3）
        try:
            closed_aliases = await page_registry.close_owned(
                (mid, target_browser_id), run.run_id
            )
            if closed_aliases:
                logger.info(
                    f"[WorkflowRunner] 运行结束已关闭页面: {closed_aliases}"
                )
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响收口
            logger.warning(f"[WorkflowRunner] 清理运行页面失败: {exc}")

        if settings.workflow_release_browser_after_run:
            # 多工作流并行：默认不回收浏览器（空闲生命周期处理）；显式开启时按下面逻辑回收
            if True:
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

    # ---------- 汇总落库 ----------
    # 时长结算与使用统计已改由终态事件订阅者执行（见 docs/工作流运行状态机计划书.md §6.5），
    # 这里只把「可计费时长」随摘要带进事件；billed_started 为空表示没拿到会话、不计费。
    # 恢复运行时 results_data 只包含本次实际执行的步骤，计数需叠加旧运行的累计值
    total = len(steps)
    # 步骤计数以「进度上报器」为唯一口径：它在每个顶层步骤 finally 里累加一次终态，
    # 断点恢复时还带着上一个运行的继承值。`results_data` 是同一批步骤的原始返回，
    # 再叠加一次就会翻倍（历史现象：total=18 却 success=36，通知里写成
    # 「成功 36 / 失败 0（共 18 步）」）。
    success_count, failed_count, new_fatal = _resolve_run_counts(
        results_data=results_data,
        progress_success=int(progress_data.success_count or 0),
        progress_failed=int(progress_data.failed_count or 0),
    )
    if not results_data and total > resume_step and status == WorkflowRunStatusEnum.SUCCESS:
        # 引擎未返回任何结果（理论上不会）：按失败处理，避免"假成功"
        status = WorkflowRunStatusEnum.FAILED
        error_message = error_message or "执行引擎未返回任何步骤结果"

    if status == WorkflowRunStatusEnum.SUCCESS and new_fatal > 0:
        status = WorkflowRunStatusEnum.FAILED
        error_message = error_message or f"存在 {new_fatal} 个失败步骤"

    # 循环中断失败：任一层循环因体内步骤失败而**提前中断** → 直接判失败，
    # 不参与下面的「容忍失败阈值 / 失败占比」判定。
    # 原因：循环被打断意味着剩余数据根本没处理，属于结构性失败；若循环步骤配了
    # on_error=continue（失败被标记 tolerated），旧的阈值逻辑在失败数未达阈值时
    # 仍会报成功，用户看到「全部成功」但实际只跑了前几条。
    if (
        status == WorkflowRunStatusEnum.SUCCESS
        and progress_reporter.loop_aborted_by_failure
    ):
        status = WorkflowRunStatusEnum.FAILED
        error_message = error_message or _build_loop_abort_message(
            loop_summaries=progress_reporter.loop_summaries,
            abort_count=progress_reporter.loop_abort_count,
        )
        logger.warning(
            f"[WorkflowRunner] 循环因失败中断，运行直接判失败（不看失败占比）: "
            f"run={run.run_id}, loop_aborts={progress_reporter.loop_abort_count}"
        )

    # ── 资源级判定（业务工作流的**主要**成败指标）──
    # 步骤成功只说明「动作跑完了」：`mark_resource_result` 记 failed 时动作本身仍然
    # 成功返回，于是全部动态都参与失败、运行却显示成功的假绿灯。
    # 有资源记录时，以资源 failed 数为主判据；资源全绿则不再看容错失败（那些是
    # 过程噪声，例如 LLM 降级），避免「同一处降级重复上百次」再次误判。
    resource_stats = await _collect_resource_stats(run.run_id)
    resource_threshold = int(settings.workflow_resource_fail_failed_threshold or 0)
    if (
        status == WorkflowRunStatusEnum.SUCCESS
        and resource_stats.has_data
        and resource_threshold > 0
        and resource_stats.failed >= resource_threshold
    ):
        status = WorkflowRunStatusEnum.FAILED
        error_message = _build_resource_failure_message(resource_stats, resource_threshold)
        logger.warning(
            f"[WorkflowRunner] 资源处理失败达到阈值，运行判失败: "
            f"run={run.run_id}, failed={resource_stats.failed}, "
            f"total={resource_stats.total}, threshold={resource_threshold}"
        )

    # 容错熔断：`on_error=continue` 允许失败，但不允许「几乎全失败还报成功」。
    # 只在**没有资源统计**（纯步骤型工作流）时生效：有资源记录时上面已按资源
    # 结论判过，过程降级不该再翻转一次结论。
    tolerated_threshold = int(settings.workflow_tolerated_fail_failed_threshold or 0)
    tolerated_count = progress_reporter.tolerated_count
    tolerating_breaker_tripped, failure_ratio = _should_trip_tolerated_breaker(
        tolerated_count=tolerated_count,
        threshold=tolerated_threshold,
        signature_count=progress_reporter.tolerated_signature_count,
        loop_summaries=progress_reporter.loop_summaries,
        max_ratio=float(settings.workflow_tolerated_fail_max_ratio or 0.0),
    )
    if (
        status == WorkflowRunStatusEnum.SUCCESS
        and not resource_stats.has_data
        and tolerating_breaker_tripped
    ):
        status = WorkflowRunStatusEnum.FAILED
        error_message = _build_tolerated_breaker_message(
            success_count=success_count,
            total=total,
            tolerated_count=tolerated_count,
            threshold=tolerated_threshold,
            loop_summaries=progress_reporter.loop_summaries,
            samples=progress_reporter.tolerated_samples,
            failure_ratio=failure_ratio,
            signature_count=progress_reporter.tolerated_signature_count,
        )
        logger.warning(
            f"[WorkflowRunner] 容错失败超过阈值，运行降级为失败: "
            f"run={run.run_id}, tolerated={tolerated_count}, "
            f"threshold={tolerated_threshold}, "
            f"ratio={failure_ratio:.3f}, "
            f"signatures={progress_reporter.tolerated_signature_count}"
        )

    duration_ms = (time.time() - started) * 1000
    # 结果摘要随终态事件下发：通知（步骤数 / 耗时 / 原因）与计费（可计费时长）都用它
    run_summary = WorkflowRunSummary(
        total=total,
        success_count=success_count,
        failed_count=failed_count,
        duration_ms=duration_ms,
        billable_seconds=(
            max(time.time() - billed_started, 0.0) if billed_started is not None else 0.0
        ),
        error_message=error_message,
        tolerated_count=progress_reporter.tolerated_count,
        tolerated_samples=progress_reporter.tolerated_samples,
        loop_summaries=progress_reporter.loop_summaries,
        resource_total=resource_stats.total,
        resource_success=resource_stats.success,
        resource_failed=resource_stats.failed,
        resource_skipped=resource_stats.skipped,
    )
    # 断点：仅「非用户主动、非执行失败」的中断才可恢复（服务重启 / 关停取消 / 崩溃）
    checkpoint_step = min(progress_reporter.data.completed_steps, total)
    resumable = (
        status == WorkflowRunStatusEnum.INTERRUPTED and 0 < checkpoint_step < total
    )
    # 终态落库失败（如状态机拒绝迁移、事件发布异常）**不能**让协程直接抛出：
    # 一抛就会跳过下面的 flush，前端永远收不到终态帧（表现为「点了停止，界面一直转」），
    # 并且 task 异常退出会让 done_callback 走异常收尾路径。这里只告警，继续收口。
    try:
        await workflow_run_crud_svr.finish(
            run_id=run.run_id,
            status=status,
            success_count=success_count,
            failed_count=failed_count,
            error_message=error_message,
            duration_ms=duration_ms,
            resumable=resumable,
            checkpoint_step=checkpoint_step,
            summary=run_summary,
        )
    except Exception as exc:  # noqa: BLE001 - 落库失败只告警，终态帧仍要推
        logger.error(
            f"[WorkflowRunner] 运行终态落库失败: run={run.run_id}, "
            f"status={status}, {type(exc).__name__}: {exc}"
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
    # 工作流卡片快照、结束通知、时长结算均由终态事件订阅者执行
    # （见 docs/工作流运行状态机计划书.md §6.2 / §6.5）

    logger.info(
        f"[WorkflowRunner] 运行结束: workflow={workflow.workflow_id}, "
        f"trigger={trigger_source}, status={status.value}, "
        f"success={success_count}/{total}, "
        f"resources={resource_stats.success}/{resource_stats.failed}/"
        f"{resource_stats.skipped}, cost={duration_ms:.0f}ms"
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
