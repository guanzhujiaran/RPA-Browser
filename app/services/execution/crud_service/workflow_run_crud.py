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
        workflow_id: str,
        mid: int | str,
        browser_id: int | str,
        trigger_source: WorkflowRunTriggerEnum | str,
        total: int = 0,
        run_id: str | None = None,
    ) -> WorkflowRunRecord:
        """创建一条 running 状态的运行记录"""
        if isinstance(trigger_source, str):
            trigger_source = WorkflowRunTriggerEnum(trigger_source)

        model = WorkflowRunRecord(
            run_id=run_id or WorkflowRunCrudService.new_run_id(),
            workflow_id=workflow_id,
            mid=str(mid),
            browser_id=str(browser_id) if browser_id is not None else "",
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
    async def get_by_run_id(run_id: str, mid: int | str | None = None) -> WorkflowRunRecord | None:
        """按运行ID查询（可选校验归属）"""
        async with DatabaseSessionManager.async_session() as session:
            query = select(WorkflowRunRecord).where(WorkflowRunRecord.run_id == run_id)
            if mid is not None:
                query = query.where(WorkflowRunRecord.mid == str(mid))
            result = await session.exec(query)
            return result.first()

    @staticmethod
    async def count_by_workflow(workflow_id: str) -> int:
        """统计某工作流的运行记录数"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(func.count(WorkflowRunRecord.id)).where(
                    WorkflowRunRecord.workflow_id == workflow_id
                )
            )
            return result.one()

    @staticmethod
    async def list_by_workflow(
        workflow_id: str,
        skip: int = 0,
        limit: int = 10,
        order_desc: bool = True,
    ) -> List[WorkflowRunRecord]:
        """按工作流分页查询运行记录（默认最新在前）"""
        async with DatabaseSessionManager.async_session() as session:
            started = WorkflowRunRecord.started_at
            query = (
                select(WorkflowRunRecord)
                .where(WorkflowRunRecord.workflow_id == workflow_id)
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
