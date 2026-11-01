"""
工作流运行记录 CRUD 服务

只承载「一次运行」的聚合视图（WorkflowRunRecord）。
步骤级明细仍在 ActionLogRecord（source=workflow + workflow_id），
通过 execution_id 关联下钻。
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List

from sqlmodel import col, func, select
from sqlalchemy import update

from app.models.database.workflow.models import (
    WorkflowRunRecord,
    WorkflowRunStatusEnum,
    WorkflowRunTriggerEnum,
)
from app.services.execution.run_events import (
    WorkflowRunSummary,
    publish_status_changed,
)
from app.services.execution.run_state_machine import (
    ensure_initial_status,
    ensure_transition,
    normalize_status,
)
from app.utils.depends.session_manager import DatabaseSessionManager


class WorkflowRunCrudService:
    """工作流运行记录 CRUD 服务"""

    @staticmethod
    def new_run_id() -> str:
        """生成运行唯一标识"""
        return uuid.uuid4().hex

    @staticmethod
    async def create_running(
        workflow_id: int,
        mid: int | str,
        browser_id: int | None,
        trigger_source: WorkflowRunTriggerEnum | str,
        total: int = 0,
        run_id: str | None = None,
        *,
        resume_of: str | None = None,
        resume_count: int = 0,
        checkpoint_step: int = 0,
        success_count: int = 0,
        failed_count: int = 0,
    ) -> WorkflowRunRecord:
        """创建一条 running 状态的运行记录

        Args:
            resume_of: 断点恢复来源的运行ID（首次运行为 None）。
            resume_count: 连续恢复次数（父运行 +1）。
            checkpoint_step: 恢复起点：旧运行已完成的步骤数（从该下标继续执行）。
            success_count / failed_count: 恢复时继承旧运行的累计计数（进度条不回退）。
        """
        if isinstance(trigger_source, str):
            trigger_source = WorkflowRunTriggerEnum(trigger_source)

        record_run_id = run_id or WorkflowRunCrudService.new_run_id()
        model = WorkflowRunRecord(
            run_id=record_run_id,
            workflow_id=int(workflow_id),
            mid=str(mid),
            browser_id=int(browser_id) if browser_id is not None else 0,
            trigger_source=trigger_source,
            # 状态迁移统一由状态机校验（见 docs/工作流运行状态机计划书.md）
            status=ensure_initial_status(
                WorkflowRunStatusEnum.RUNNING, run_id=record_run_id
            ),
            total=total,
            started_at=datetime.now(),
            resume_of=resume_of,
            resume_count=resume_count,
            checkpoint_step=checkpoint_step,
            completed_steps=checkpoint_step,
            success_count=success_count,
            failed_count=failed_count,
        )
        async with DatabaseSessionManager.async_session() as session:
            session.add(model)
            await session.commit()
            await session.refresh(model)
        # 落库成功后再发事件（见 docs/工作流运行状态机计划书.md §7）
        publish_status_changed(
            model.run_id, WorkflowRunStatusEnum.RUNNING, reason="创建运行记录"
        )
        return model

    @staticmethod
    async def create_queued(
        workflow_id: int,
        mid: int | str,
        browser_id: int | None,
        trigger_source: WorkflowRunTriggerEnum | str,
        run_id: str,
    ) -> WorkflowRunRecord:
        """创建一条 queued 状态记录（排队凭证）。

        排队期间前端即可看到这次运行（``status=queued``），进程重启后
        ``list_queued()`` 会把它重新入队，因此**必须落库**，不能只放内存。
        """
        if isinstance(trigger_source, str):
            trigger_source = WorkflowRunTriggerEnum(trigger_source)

        model = WorkflowRunRecord(
            run_id=run_id,
            workflow_id=int(workflow_id),
            mid=str(mid),
            browser_id=int(browser_id) if browser_id is not None else 0,
            trigger_source=trigger_source,
            status=ensure_initial_status(WorkflowRunStatusEnum.QUEUED, run_id=run_id),
            total=0,
            # 受理即写入 started_at：运行列表按它排序，排队项也要有稳定顺序
            started_at=datetime.now(),
        )
        async with DatabaseSessionManager.async_session() as session:
            session.add(model)
            await session.commit()
            await session.refresh(model)
        publish_status_changed(model.run_id, WorkflowRunStatusEnum.QUEUED, reason="创建排队记录")
        return model

    @staticmethod
    async def mark_running(
        run_id: str,
        *,
        total: int,
        resume_of: str | None = None,
        resume_count: int = 0,
        checkpoint_step: int = 0,
        success_count: int = 0,
        failed_count: int = 0,
    ) -> WorkflowRunRecord | None:
        """把排队中的记录转为 running（拿到执行权后调用）。

        Returns:
            更新后的记录；记录不存在或已不是 QUEUED（被取消 / 超时）时返回 None。
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRunRecord).where(WorkflowRunRecord.run_id == run_id)
            )
            model = result.first()
            if model is None or model.status != WorkflowRunStatusEnum.QUEUED:
                return None

            model.status = ensure_transition(
                model.status, WorkflowRunStatusEnum.RUNNING, run_id=run_id
            )
            model.total = total
            model.started_at = datetime.now()
            model.resume_of = resume_of
            model.resume_count = resume_count
            model.checkpoint_step = checkpoint_step
            model.completed_steps = checkpoint_step
            model.success_count = success_count
            model.failed_count = failed_count
            session.add(model)
            await session.commit()
            await session.refresh(model)
        publish_status_changed(
            run_id,
            WorkflowRunStatusEnum.RUNNING,
            from_status=WorkflowRunStatusEnum.QUEUED,
            reason="取得执行权，开始运行",
        )
        return model

    @staticmethod
    async def list_queued() -> List[WorkflowRunRecord]:
        """列出所有排队中的运行（按提交顺序，即主键升序）。"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRunRecord)
                .where(WorkflowRunRecord.status == WorkflowRunStatusEnum.QUEUED)
                .order_by(col(WorkflowRunRecord.id))
            )
            return list(result.all())

    @staticmethod
    async def finish_queued(
        run_id: str,
        *,
        status: WorkflowRunStatusEnum,
        error_message: str | None = None,
    ) -> bool:
        """把排队中的记录直接落终态（排队取消 / 超时）。

        只对仍为 QUEUED 的记录生效，避免覆盖已经开始执行的运行。
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRunRecord).where(WorkflowRunRecord.run_id == run_id)
            )
            model = result.first()
            if model is None or model.status != WorkflowRunStatusEnum.QUEUED:
                return False

            previous = model.status
            model.status = ensure_transition(model.status, status, run_id=run_id)
            model.error_message = error_message
            model.finished_at = datetime.now()
            session.add(model)
            await session.commit()
        publish_status_changed(
            run_id,
            status,
            from_status=previous,
            reason=error_message or "排队记录终止",
        )
        return True

    @staticmethod
    async def list_active_of_workflow(
        workflow_id: int,
        mid: int | str,
        *,
        exclude_run_id: str = "",
    ) -> List[WorkflowRunRecord]:
        """列出某工作流仍在进行中的运行（QUEUED / RUNNING）。

        停止工作流时，除中断当前运行外，同工作流可能还有其它浏览器目标的运行
        （多选浏览器 / 定时触发），它们若继续执行等于「停止没生效」。

        Args:
            exclude_run_id: 需要排除的运行ID（通常是本次已单独处理的运行）。
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRunRecord).where(
                    WorkflowRunRecord.workflow_id == int(workflow_id),
                    WorkflowRunRecord.mid == str(mid),
                    WorkflowRunRecord.status.in_(
                        [WorkflowRunStatusEnum.QUEUED, WorkflowRunStatusEnum.RUNNING]
                    ),
                )
            )
            return [
                model
                for model in result.all()
                if model.run_id and model.run_id != exclude_run_id
            ]

    @staticmethod
    async def get_queue_context(run_id: str) -> tuple[int, int] | None:
        """取运行所属 (mid, browser_id)，供取消排队时定位队列键。"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRunRecord).where(WorkflowRunRecord.run_id == run_id)
            )
            model = result.first()
            if model is None:
                return None
            return int(model.mid), int(model.browser_id)

    @staticmethod
    async def update_progress(
        run_id: str,
        *,
        completed_steps: int,
        current_step_index: int,
        current_step_name: str,
        loop_action_name: str,
        loop_index: int,
        loop_total: int,
        success_count: int,
        failed_count: int,
        loop_body_completed: int = 0,
        loop_body_total: int = 0,
        loop_body_step_name: str = "",
    ) -> bool:
        """写入运行期进度快照（仅 RUNNING 命中，避免覆盖已收口的终态）

        终态（状态/耗时/结束时间）由 ``finish()`` 统一写入，避免两处来源。
        """
        values: dict[str, Any] = {
            "completed_steps": completed_steps,
            "current_step_index": current_step_index,
            "current_step_name": current_step_name,
            "loop_action_name": loop_action_name,
            "loop_index": loop_index,
            "loop_total": loop_total,
            "success_count": success_count,
            "failed_count": failed_count,
            "progress_updated_at": datetime.now(),
        }
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                update(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.run_id == run_id,
                    WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING,
                )
                .values(**values)
            )
            await session.commit()
            return bool(getattr(result, "rowcount", 1))

    @staticmethod
    async def mark_resume_consumed(run_id: str) -> bool:
        """标记某条中断运行已被恢复（保证同一条只被恢复一次）"""
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(WorkflowRunRecord)
                .where(WorkflowRunRecord.run_id == run_id)
                .values(resumable=False)
            )
            await session.commit()
            return True

    @staticmethod
    async def list_resumable(
        *, max_age_seconds: int, limit: int = 50
    ) -> List[WorkflowRunRecord]:
        """取「可恢复且未被恢复过」的中断运行（按开始时间升序，先中断的先恢复）"""
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
        async with DatabaseSessionManager.async_session() as session:
            rows = (
                await session.exec(
                    select(WorkflowRunRecord)
                    .where(
                        WorkflowRunRecord.status == WorkflowRunStatusEnum.INTERRUPTED,
                        WorkflowRunRecord.resumable == True,  # noqa: E712 - SQL 条件
                        WorkflowRunRecord.finished_at.is_not(None),
                        WorkflowRunRecord.finished_at >= cutoff,
                    )
                    .order_by(WorkflowRunRecord.started_at.asc())
                    .limit(limit)
                )
            ).all()
            return list(rows)

    @staticmethod
    async def finish(
        run_id: str,
        status: WorkflowRunStatusEnum | str,
        success_count: int = 0,
        failed_count: int = 0,
        execution_id: str = "",
        error_message: str | None = None,
        duration_ms: float = 0.0,
        *,
        resumable: bool = False,
        checkpoint_step: int = 0,
        summary: WorkflowRunSummary | None = None,
    ) -> bool:
        """写入运行结束状态（CAS：仅当记录仍为 ``RUNNING`` 时命中）

        Args:
            summary: 运行结果摘要，随终态事件下发给订阅者（通知 / 计费），
                见 ``docs/工作流运行状态机计划书.md`` §6.5。

        状态转移以「比较并交换」实现——``UPDATE ... WHERE run_id=? AND status='RUNNING'``，
        避免与兜底收口（``interrupt_stale_running``）并发时互相覆盖终态。

        Returns:
            True 表示本次成功把状态从 RUNNING 推进到终态；False 表示记录已终结或
            不存在（本次为幂等空操作）。
        """
        status = normalize_status(status)
        # 运行中只能迁到终态：非法目标在这里直接报错，不落 SQL
        ensure_transition(WorkflowRunStatusEnum.RUNNING, status, run_id=run_id)

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                update(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.run_id == run_id,
                    WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING,
                )
                .values(
                    status=status,
                    success_count=success_count,
                    failed_count=failed_count,
                    error_message=(error_message or None),
                    duration_ms=duration_ms,
                    finished_at=datetime.now(),
                    resumable=resumable,
                    checkpoint_step=checkpoint_step,
                    progress_updated_at=datetime.now(),
                )
            )
            await session.commit()
            # rowcount 不可得（部分驱动/封装不暴露）时按「命中」处理：终态由 WHERE
            # 子句保证唯一，返回值仅用于日志与短路，不影响正确性。
            hit = bool(getattr(result, "rowcount", 1))
        if hit:
            # 只在真正推进到终态时发事件（幂等空操作不产生重复事件）
            publish_status_changed(
                run_id,
                status,
                from_status=WorkflowRunStatusEnum.RUNNING,
                reason=error_message or "运行收口",
                summary=summary,
            )
        return hit

    @staticmethod
    async def get_running_for_browser(
        mid: int | str, browser_id: int
    ) -> str | None:
        """取该用户该浏览器当前进行中的运行ID（恢复前做并发拦截）"""
        async with DatabaseSessionManager.async_session() as session:
            row = (
                await session.exec(
                    select(WorkflowRunRecord.run_id)
                    .where(
                        WorkflowRunRecord.mid == str(mid),
                        WorkflowRunRecord.browser_id == int(browser_id),
                        WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING,
                    )
                    .limit(1)
                )
            ).first()
            return row

    @staticmethod
    async def mark_notified(run_id: str) -> bool:
        """标记失败通知已发送（防重复推送）"""
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(WorkflowRunRecord)
                .where(WorkflowRunRecord.run_id == run_id)
                .values(notified=True)
            )
            await session.commit()
            return True

    @staticmethod
    async def interrupt_stale_running(
        reason: str = "服务重启，运行被中断",
        run_ids: List[str] | None = None,
    ) -> List[WorkflowRunRecord]:
        """把残留的 RUNNING 记录收口为 INTERRUPTED

        进程非正常退出（kill / 宕机 / 崩溃）时，正在执行的工作流不会走 finish()，
        其运行记录会永久停留在 RUNNING。这会导致：
          1. 同浏览器并发防护（browser_id + RUNNING）误判，该浏览器工作流再无法启动；
          2. 计费心跳等按 RUNNING 扫描的逻辑把已死的运行当作存活。
        因此必须在启动重建定时任务前统一收口。资源级断点续跑由
        WorkflowResourceRecord 保证（success 跳过 / failed 下次重试），此处不重跑。

        Args:
            reason: 写入 error_message 的收口原因。
            run_ids: 仅收口指定的运行（传 None 表示收口全部 RUNNING）。启动恢复传
                None；运行期兜底回收传「本实例已无对应执行会话」的僵尸运行 id，
                避免误伤仍在执行的运行。

        计费安全：
          - 已结算（settled_seconds 非 None，通常是停机前计费心跳的兜底结算）的运行
            保持原状，仅在仍为 RUNNING 时改状态，绝不回退其结算结果；
          - 未结算的定时（SCHEDULE）运行，复用计费心跳的「会话丢失兜底结算」逻辑，
            按实际经过时长补扣后再置中断，避免重启成为逃费口子；
          - 手动（MANUAL）运行本就不计费，直接置中断。

        返回被收口的记录列表（供日志 / 审计）。
        """
        import math

        from loguru import logger

        from app.models.database.workflow.models import WorkflowRunTriggerEnum
        from app.services.membership.membership_service import MembershipService
        from app.utils.time_util import now_local

        if run_ids is not None and not run_ids:
            return []

        now = now_local()
        interrupted: List[WorkflowRunRecord] = []

        # 读取本次需要收口的 RUNNING 记录；读取失败则无从收口，直接返回
        async with DatabaseSessionManager.async_session() as session:
            stmt = select(WorkflowRunRecord).where(
                WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING
            )
            if run_ids is not None:
                stmt = stmt.where(WorkflowRunRecord.run_id.in_(run_ids))
            stale = (await session.exec(stmt)).all()
        if not stale:
            return []

        # 已结算的运行（计费心跳兜底结算过）：只改状态，不动结算字段。
        # 单独 try：批量更新失败也不能阻断后续逐条与最终兜底收口。
        settled = [r for r in stale if r.settled_seconds is not None]
        if settled:
            try:
                # 批量迁移同样过状态机：RUNNING → INTERRUPTED 是唯一目标
                ensure_transition(
                    WorkflowRunStatusEnum.RUNNING,
                    WorkflowRunStatusEnum.INTERRUPTED,
                )
                async with DatabaseSessionManager.async_session() as session:
                    settled_ids = [r.run_id for r in settled]
                    await session.exec(
                        update(WorkflowRunRecord)
                        .where(WorkflowRunRecord.run_id.in_(settled_ids))
                        .values(
                            status=WorkflowRunStatusEnum.INTERRUPTED,
                            error_message=reason,
                            finished_at=now,
                        )
                    )
                    # 断点恢复标记：有过已完成步骤才可恢复（completed_steps 已是检查点）
                    resumable_ids = [
                        r.run_id for r in settled if (r.completed_steps or 0) > 0
                    ]
                    if resumable_ids:
                        await session.exec(
                            update(WorkflowRunRecord)
                            .where(WorkflowRunRecord.run_id.in_(resumable_ids))
                            .values(
                                resumable=True,
                                checkpoint_step=WorkflowRunRecord.completed_steps,
                            )
                        )
                    await session.commit()
                    interrupted.extend(settled)
                # 提交后再发事件（旁路：失败不影响收口）
                for done in settled:
                    publish_status_changed(
                        done.run_id,
                        WorkflowRunStatusEnum.INTERRUPTED,
                        from_status=WorkflowRunStatusEnum.RUNNING,
                        reason=reason,
                    )
            except Exception as exc:  # noqa: BLE001 - 转最终兜底收口
                logger.warning(
                    f"[WorkflowRun] 批量收口已结算运行失败，转最终兜底: {exc}"
                )

        # 未结算运行逐条处理（行锁 + 计费，独立事务，单条失败不影响整体恢复）
        unsettled = [r for r in stale if r.settled_seconds is None]
        for run in unsettled:
            try:
                async with DatabaseSessionManager.async_session() as session:
                    current = (
                        await session.exec(
                            select(WorkflowRunRecord)
                            .where(WorkflowRunRecord.run_id == run.run_id)
                            .with_for_update()
                        )
                    ).first()
                    if (
                        current is None
                        or current.status != WorkflowRunStatusEnum.RUNNING
                        or current.settled_seconds is not None
                    ):
                        continue

                    run_seconds = (now - current.started_at).total_seconds()

                    # 仅定时任务计费：会话已随进程消失，按整分钟兜底补扣。
                    # 计费是**旁路关注点**（side-car），其失败绝不能劫持生命周期收口：
                    # 否则该记录会永久停留在 RUNNING，每次重启/回收都重试并再次失败，
                    # 且持续拦截该浏览器的新运行（用户痛点根因）。
                    billing_error: str | None = None
                    if current.trigger_source == WorkflowRunTriggerEnum.SCHEDULE:
                        try:
                            mid = int(current.mid)
                            already_charged = await MembershipService._sum_run_charged(
                                session, current.run_id
                            )
                            target_total = math.ceil(max(run_seconds, 0) / 60.0) * 60
                            delta = max(target_total - already_charged, 0)
                            charged = await MembershipService._charge_run_delta(
                                mid,
                                delta,
                                session,
                                workflow_id=current.workflow_id,
                                run_id=current.run_id,
                                browser_id=(
                                    int(current.browser_id) if current.browser_id else 0
                                ),
                                remark="服务重启中断，启动恢复兜底结算",
                            )
                            current.settled_seconds = already_charged + charged
                            await MembershipService.record_usage(
                                mid,
                                int(current.browser_id) if current.browser_id else 0,
                                run_seconds,
                                session,
                                is_workflow=True,
                                is_scheduled=True,
                                now=now,
                            )
                        except Exception as billing_exc:  # noqa: BLE001 - 计费失败不阻断收口
                            billing_error = (
                                f"{type(billing_exc).__name__}: {billing_exc}"
                            )
                            logger.warning(
                                f"[WorkflowRun] 计费兜底失败，仍按中断收口: "
                                f"run={run.run_id}, {billing_exc}"
                            )

                    current.status = ensure_transition(
                        current.status,
                        WorkflowRunStatusEnum.INTERRUPTED,
                        run_id=run.run_id,
                    )
                    current.error_message = (
                        f"{reason}（计费兜底失败: {billing_error}）"
                        if billing_error
                        else reason
                    )
                    current.finished_at = now
                    current.duration_ms = run_seconds * 1000
                    # 断点恢复标记：只对「有已完成步骤」的中断运行开放恢复；
                    # 用户手动停止走 CANCELLED 分支，不会进入本函数，故无需额外排除。
                    current.checkpoint_step = current.completed_steps
                    current.resumable = current.completed_steps > 0
                    session.add(current)
                    await session.commit()
                    # commit 会 expire 实例；刷新后再返回，避免调用方访问时
                    # 触发 DetachedInstanceError（会话已退出）
                    await session.refresh(current)
                    interrupted.append(current)
                publish_status_changed(
                    current.run_id,
                    WorkflowRunStatusEnum.INTERRUPTED,
                    from_status=WorkflowRunStatusEnum.RUNNING,
                    reason=reason,
                )
            except Exception as exc:  # noqa: BLE001 - 取行/提交等基础设施异常
                logger.warning(
                    f"[WorkflowRun] 逐条收口事务失败，改用兜底强制收口: "
                    f"run={run.run_id}, {exc}"
                )
                # 兜底：不经计费，CAS 强制置中断，保证「监督者永远负责收尸」
                if await WorkflowRunCrudService._force_interrupt_run(run.run_id, reason):
                    interrupted.append(run)

        # 最终保险：对本次识别出的 stale 运行逐一做 CAS 强制收口（幂等）。
        # 无论前面哪一阶段失败（批量更新、逐条事务、计费），都不留 RUNNING 残留：
        # 已被收口的记录不会被再次命中（CAS 的 WHERE status='RUNNING' 保证）。
        reaped_ids = {r.run_id for r in interrupted}
        for run in stale:
            if run.run_id in reaped_ids:
                continue
            if await WorkflowRunCrudService._force_interrupt_run(run.run_id, reason):
                interrupted.append(run)

        return interrupted

    @staticmethod
    async def _force_interrupt_run(run_id: str, reason: str) -> bool:
        """兜底收口：不经计费，CAS 把仍为 RUNNING 的记录强制置为 INTERRUPTED

        当逐条收口事务因基础设施异常（行锁超时 / 提交失败等）失败时使用，保证运行
        记录不会永久停留在 RUNNING。计费旁路此时被放弃，由计费心跳后续兜底。
        """
        from app.utils.time_util import now_local

        # 兜底收口的迁移是固定的 RUNNING → INTERRUPTED，仍走状态机保持单一规则来源
        ensure_transition(
            WorkflowRunStatusEnum.RUNNING,
            WorkflowRunStatusEnum.INTERRUPTED,
            run_id=run_id,
        )
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                update(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.run_id == run_id,
                    WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING,
                )
                .values(
                    status=WorkflowRunStatusEnum.INTERRUPTED,
                    error_message=f"{reason}（逐条收口失败，兜底强制收口）",
                    finished_at=now_local(),
                    # 兜底路径同样保留断点：完成过步骤即可恢复
                    resumable=WorkflowRunRecord.completed_steps > 0,
                    checkpoint_step=WorkflowRunRecord.completed_steps,
                )
            )
            await session.commit()
            hit = bool(getattr(result, "rowcount", 1))
        if hit:
            publish_status_changed(
                run_id,
                WorkflowRunStatusEnum.INTERRUPTED,
                from_status=WorkflowRunStatusEnum.RUNNING,
                reason=reason,
            )
        return hit

    @staticmethod
    async def get_by_run_id(
        run_id: str, mid: int | str | None = None
    ) -> WorkflowRunRecord | None:
        """按运行ID查询（可选校验归属）"""
        async with DatabaseSessionManager.async_session() as session:
            query = select(WorkflowRunRecord).where(WorkflowRunRecord.run_id == run_id)
            if mid is not None:
                query = query.where(WorkflowRunRecord.mid == str(mid))
            result = await session.exec(query)
            return result.first()

    @staticmethod
    async def count_by_workflow(workflow_id: int) -> int:
        """统计某工作流的运行记录数"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(func.count(WorkflowRunRecord.id)).where(
                    WorkflowRunRecord.workflow_id == int(workflow_id)
                )
            )
            return result.one()

    @staticmethod
    async def list_by_workflow(
        workflow_id: int,
        skip: int = 0,
        limit: int = 10,
        order_desc: bool = True,
    ) -> List[WorkflowRunRecord]:
        """按工作流分页查询运行记录（默认最新在前）"""
        async with DatabaseSessionManager.async_session() as session:
            started = WorkflowRunRecord.started_at
            query = (
                select(WorkflowRunRecord)
                .where(WorkflowRunRecord.workflow_id == int(workflow_id))
                .order_by(started.desc() if order_desc else started.asc())
                .offset(skip)
                .limit(limit)
            )
            result = await session.exec(query)
            return result.all()

    @staticmethod
    async def list_running_run_ids(workflow_ids: List[int]) -> Dict[int, str]:
        """批量取「当前进行中」的运行ID（RUNNING）

        工作流卡片用：一次查询得到 workflow_id -> run_id 映射，避免逐条查询 N+1。
        并发防护下同一工作流正常只有一条 RUNNING；若存在历史残留多条，
        按 started_at 倒序保留最新的一条。
        """
        if not workflow_ids:
            return {}
        async with DatabaseSessionManager.async_session() as session:
            query = (
                select(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING,
                    WorkflowRunRecord.workflow_id.in_(
                        [int(w) for w in workflow_ids]
                    ),
                )
                .order_by(WorkflowRunRecord.started_at.desc())
            )
            result = await session.exec(query)
            rows = result.all()
        running: Dict[int, str] = {}
        for row in rows:
            running.setdefault(row.workflow_id, row.run_id)
        return running

    @staticmethod
    async def to_dict(model: WorkflowRunRecord) -> Dict[str, Any]:
        """转换为响应字典（供路由层复用）"""
        return {
            "id": model.id,
            "run_id": model.run_id,
            "workflow_id": model.workflow_id,
            "browser_id": model.browser_id,
            "trigger_source": model.trigger_source,
            "status": model.status,
            "total": model.total,
            "success_count": model.success_count,
            "failed_count": model.failed_count,
            # 执行批次ID 不再单独落库：工作流运行场景下它恒等于 run_id，
            # 对外仍以 execution_id 字段暴露，与操作日志 / 已处理数据同名同值
            "execution_id": model.run_id,
            "error_message": model.error_message,
            "duration_ms": model.duration_ms,
            "started_at": model.started_at,
            "finished_at": model.finished_at,
            # 运行期进度（SSE 首帧 / 终态帧与列表展示共用同一份快照）
            "completed_steps": model.completed_steps,
            "current_step_index": model.current_step_index,
            "current_step_name": model.current_step_name,
            "loop_action_name": model.loop_action_name,
            "loop_index": model.loop_index,
            "loop_total": model.loop_total,
            "loop_body_completed": model.loop_body_completed,
            "loop_body_total": model.loop_body_total,
            "loop_body_step_name": model.loop_body_step_name,
            "progress_updated_at": model.progress_updated_at,
            # 断点恢复
            "resumable": model.resumable,
            "resume_of": model.resume_of,
            "resume_count": model.resume_count,
            "checkpoint_step": model.checkpoint_step,
        }


workflow_run_crud_svr = WorkflowRunCrudService()
