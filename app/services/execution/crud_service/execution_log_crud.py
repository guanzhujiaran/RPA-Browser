"""
执行日志 CRUD 服务
"""
from typing import Any, Dict, List
from datetime import datetime
import uuid
from sqlmodel import select, update, func

from app.models.database.workflow.models import WorkflowExecutionLog
from app.utils.depends.session_manager import DatabaseSessionManager


class ExecutionLogCrudService:
    """执行日志 CRUD 服务"""

    @staticmethod
    async def create(
        mid: int,
        execution_id: str,
        session_id: str,
        browser_id: str,
        workflow_id: str = "",
        steps_count: int = 0,
    ) -> WorkflowExecutionLog:
        async with DatabaseSessionManager.async_session() as session:
            model = WorkflowExecutionLog(
                workflow_id=workflow_id,
                session_id=session_id,
                browser_id=browser_id,
                mid=mid,
                execution_id=execution_id,
                status="running",
                steps_count=steps_count,
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def get_by_id(id: int) -> WorkflowExecutionLog | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLog).where(WorkflowExecutionLog.id == id)
            )
            return result.first()

    @staticmethod
    async def update_results(
        id: int,
        results: List[Dict],
        status: str,
        total_time: float,
    ) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLog).where(WorkflowExecutionLog.id == id)
            )
            model = result.first()
            if not model:
                return False

            model.status = status
            model.results = results
            model.total_time = total_time
            model.finished_at = datetime.now()
            await session.commit()
            return True

    @staticmethod
    async def delete(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLog).where(WorkflowExecutionLog.id == id)
            )
            model = result.first()
            if not model:
                return False
            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def count_by_user(
        mid: int,
        workflow_id: str | None = None,
        status: str | None = None,
    ) -> int:
        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(WorkflowExecutionLog.id)).where(
                WorkflowExecutionLog.mid == mid
            )
            if workflow_id:
                query = query.where(WorkflowExecutionLog.workflow_id == workflow_id)
            if status:
                query = query.where(WorkflowExecutionLog.status == status)

            result = await session.exec(query)
            return result.one()

    @staticmethod
    async def list_by_user(
        mid: int,
        skip: int = 0,
        limit: int = 100,
        workflow_id: str | None = None,
        status: str | None = None,
    ) -> List[WorkflowExecutionLog]:
        async with DatabaseSessionManager.async_session() as session:
            query = select(WorkflowExecutionLog).where(
                WorkflowExecutionLog.mid == mid
            )
            if workflow_id:
                query = query.where(WorkflowExecutionLog.workflow_id == workflow_id)
            if status:
                query = query.where(WorkflowExecutionLog.status == status)

            query = query.order_by(WorkflowExecutionLog.started_at.desc())
            query = query.offset(skip).limit(limit)
            result = await session.exec(query)
            return result.all()

    @staticmethod
    async def count_by_execution_id(execution_id: str) -> int:
        """统计特定执行批次的日志数量"""
        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(WorkflowExecutionLog.id)).where(
                WorkflowExecutionLog.execution_id == execution_id
            )
            result = await session.exec(query)
            return result.one()

    @staticmethod
    async def list_by_execution_id(
        execution_id: str, skip: int = 0, limit: int = 100
    ) -> List[WorkflowExecutionLog]:
        """获取特定执行批次的日志列表"""
        async with DatabaseSessionManager.async_session() as session:
            query = (
                select(WorkflowExecutionLog)
                .where(WorkflowExecutionLog.execution_id == execution_id)
                .order_by(WorkflowExecutionLog.started_at.asc())
                .offset(skip)
                .limit(limit)
            )
            result = await session.exec(query)
            return result.all()


execution_log_crud_svr = ExecutionLogCrudService()
