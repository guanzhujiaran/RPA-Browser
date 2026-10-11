"""
工作流运行记录 CRUD 服务

只承载「一次运行」的聚合视图（WorkflowRunRecord）。
步骤级明细仍在 ActionLogRecord（source=workflow + workflow_id），
通过 execution_id 关联下钻。
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List

from sqlmodel import func, select
from sqlalchemy import update

from app.models.database.workflow.models import (
    WorkflowRunRecord,
    WorkflowRunStatusEnum,
    WorkflowRunTriggerEnum,
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
    ) -> WorkflowRunRecord:
        """创建一条 running 状态的运行记录"""
        if isinstance(trigger_source, str):
            trigger_source = WorkflowRunTriggerEnum(trigger_source)

        model = WorkflowRunRecord(
            run_id=run_id or WorkflowRunCrudService.new_run_id(),
            workflow_id=int(workflow_id),
            mid=str(mid),
            browser_id=int(browser_id) if browser_id is not None else 0,
            trigger_source=trigger_source,
            status=WorkflowRunStatusEnum.RUNNING,
            total=total,
            started_at=datetime.now(),
        )
        async with DatabaseSessionManager.async_session() as session:
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def finish(
        run_id: str,
        status: WorkflowRunStatusEnum | str,
        success_count: int = 0,
        failed_count: int = 0,
        execution_id: str = "",
        error_message: str | None = None,
        duration_ms: float = 0.0,
    ) -> bool:
        """写入运行结束状态"""
        if isinstance(status, str):
            status = WorkflowRunStatusEnum(status)

        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(WorkflowRunRecord)
                .where(WorkflowRunRecord.run_id == run_id)
                .values(
                    status=status,
                    success_count=success_count,
                    failed_count=failed_count,
                    execution_id=execution_id,
                    error_message=(error_message or None),
                    duration_ms=duration_ms,
                    finished_at=datetime.now(),
                )
            )
            await session.commit()
            return True

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
    ) -> List[WorkflowRunRecord]:
        """启动恢复：把残留的 RUNNING 记录收口为 INTERRUPTED

        进程非正常退出（kill / 宕机 / 崩溃）时，正在执行的工作流不会走 finish()，
        其运行记录会永久停留在 RUNNING。这会导致：
          1. 同浏览器并发防护（browser_id + RUNNING）误判，该浏览器工作流再无法启动；
          2. 计费心跳等按 RUNNING 扫描的逻辑把已死的运行当作存活。
        因此必须在启动重建定时任务前统一收口。资源级断点续跑由
        WorkflowResourceRecord 保证（success 跳过 / failed 下次重试），此处不重跑。

        计费安全：
          - 已结算（settled_seconds 非 None，通常是停机前计费心跳的兜底结算）的运行
            保持原状，仅在仍为 RUNNING 时改状态，绝不回退其结算结果；
          - 未结算的定时（SCHEDULE）运行，复用计费心跳的「会话丢失兜底结算」逻辑，
            按实际经过时长补扣后再置中断，避免重启成为逃费口子；
          - 手动（MANUAL）运行本就不计费，直接置中断。

        返回被收口的记录列表（供日志 / 审计）。
        """
        import math

        from app.models.database.workflow.models import WorkflowRunTriggerEnum
        from app.services.membership.membership_service import MembershipService
        from app.utils.time_util import now_local

        now = now_local()
        interrupted: List[WorkflowRunRecord] = []

        async with DatabaseSessionManager.async_session() as session:
            stale = (
                await session.exec(
                    select(WorkflowRunRecord).where(
                        WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING
                    )
                )
            ).all()
            if not stale:
                return []

            # 已结算的运行（计费心跳兜底结算过）：只改状态，不动结算字段
            settled_ids = [r.run_id for r in stale if r.settled_seconds is not None]
            if settled_ids:
                await session.exec(
                    update(WorkflowRunRecord)
                    .where(WorkflowRunRecord.run_id.in_(settled_ids))
                    .values(
                        status=WorkflowRunStatusEnum.INTERRUPTED,
                        error_message=reason,
                        finished_at=now,
                    )
                )
                interrupted.extend([r for r in stale if r.run_id in settled_ids])

            await session.commit()

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

                    # 仅定时任务计费：会话已随进程消失，按整分钟兜底补扣
                    if current.trigger_source == WorkflowRunTriggerEnum.SCHEDULE:
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

                    current.status = WorkflowRunStatusEnum.INTERRUPTED
                    current.error_message = reason
                    current.finished_at = now
                    current.duration_ms = run_seconds * 1000
                    session.add(current)
                    await session.commit()
                    interrupted.append(current)
            except Exception as exc:  # noqa: BLE001 - 单条收口失败不阻塞其余恢复
                from loguru import logger

                logger.warning(
                    f"[WorkflowRun] 收口中断运行失败，保留 RUNNING 待人工/心跳处理: "
                    f"run={run.run_id}, {exc}"
                )

        return interrupted

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
            "execution_id": model.execution_id,
            "error_message": model.error_message,
            "duration_ms": model.duration_ms,
            "started_at": model.started_at,
            "finished_at": model.finished_at,
        }


workflow_run_crud_svr = WorkflowRunCrudService()
