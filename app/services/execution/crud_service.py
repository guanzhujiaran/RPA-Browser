"""
执行模块 CRUD 服务

提供操作、插件、工作流的完整 CRUD 功能
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import uuid
from sqlmodel import select

from app.models.core.workflow.models import (
    CustomActionModel,
    UserPluginModel,
    UserWorkflowModel,
    WorkflowExecutionLogModel,
)
from app.utils.depends.session_manager import DatabaseSessionManager


class ActionCrudService:
    """操作 CRUD 服务"""

    @staticmethod
    async def create(
        mid: str,
        action_id: str,
        name: str,
        action_type: str = "composite",
        parameters_schema: List[Dict[str, Any]] = None,
        steps: List[Dict[str, Any]] = None,
        is_composite: bool = False,
        code: Optional[str] = None,
        description: str = "",
        tags: Optional[List[str]] = None,
        timeout: int = 30000,
    ) -> CustomActionModel:
        """创建自定义操作"""
        async with DatabaseSessionManager.async_session() as session:
            model = CustomActionModel(
                action_id=action_id,
                name=name,
                action_type=action_type,
                description=description,
                mid=mid,
                timeout=timeout,
                is_composite=is_composite,
            )
            model.set_parameters_schema(parameters_schema or [])
            model.set_steps(steps or [])
            model.set_tags(tags or [])
            if code:
                model.code = code
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def get_by_id(id: int) -> Optional[CustomActionModel]:
        """根据ID获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomActionModel).where(CustomActionModel.id == id))
            return result.first()

    @staticmethod
    async def list_by_user(mid: str, skip: int = 0, limit: int = 100) -> List[CustomActionModel]:
        """获取用户的所有操作"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CustomActionModel)
                .where(CustomActionModel.mid == mid)
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def update(
        id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        parameters_schema: Optional[List[Dict[str, Any]]] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        is_composite: Optional[bool] = None,
        code: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Optional[CustomActionModel]:
        """更新操作"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomActionModel).where(CustomActionModel.id == id))
            model = result.first()
            if not model:
                return None

            if name is not None:
                model.name = name
            if description is not None:
                model.description = description
            if parameters_schema is not None:
                model.set_parameters_schema(parameters_schema)
            if steps is not None:
                model.set_steps(steps)
            if is_composite is not None:
                model.is_composite = is_composite
            if code is not None:
                model.code = code
            if timeout is not None:
                model.timeout = timeout

            model.updated_at = datetime.now()
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def delete(id: int) -> bool:
        """删除操作"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomActionModel).where(CustomActionModel.id == id))
            model = result.first()
            if not model:
                return False
            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        """启用操作"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomActionModel).where(CustomActionModel.id == id))
            model = result.first()
            if not model:
                return False
            model.is_enabled = True
            model.updated_at = datetime.now()
            await session.commit()
            return True

    @staticmethod
    async def disable(id: int) -> bool:
        """禁用操作"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomActionModel).where(CustomActionModel.id == id))
            model = result.first()
            if not model:
                return False
            model.is_enabled = False
            model.updated_at = datetime.now()
            await session.commit()
            return True


class WorkflowCrudService:
    """工作流 CRUD 服务"""

    @staticmethod
    async def create(
        mid: str,
        workflow_id: str,
        name: str,
        steps: List[Dict[str, Any]],
        description: str = "",
        on_error: str = "stop",
        tags: Optional[List[str]] = None,
    ) -> UserWorkflowModel:
        """创建工作流"""
        async with DatabaseSessionManager.async_session() as session:
            model = UserWorkflowModel(
                workflow_id=workflow_id,
                name=name,
                description=description,
                on_error=on_error,
                mid=mid,
            )
            model.set_steps(steps)
            model.set_tags(tags or [])
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def get_by_id(id: int) -> Optional[UserWorkflowModel]:
        """根据ID获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflowModel).where(UserWorkflowModel.id == id))
            return result.first()

    @staticmethod
    async def list_by_user(mid: str, skip: int = 0, limit: int = 100) -> List[UserWorkflowModel]:
        """获取用户的所有工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflowModel)
                .where(UserWorkflowModel.mid == mid)
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def update(
        id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        on_error: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[UserWorkflowModel]:
        """更新工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflowModel).where(UserWorkflowModel.id == id))
            model = result.first()
            if not model:
                return None

            if name is not None:
                model.name = name
            if description is not None:
                model.description = description
            if steps is not None:
                model.set_steps(steps)
            if on_error is not None:
                model.on_error = on_error
            if tags is not None:
                model.set_tags(tags)

            model.updated_at = datetime.now()
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def delete(id: int) -> bool:
        """删除工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflowModel).where(UserWorkflowModel.id == id))
            model = result.first()
            if not model:
                return False
            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        """启用工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflowModel).where(UserWorkflowModel.id == id))
            model = result.first()
            if not model:
                return False
            model.is_enabled = True
            model.updated_at = datetime.now()
            await session.commit()
            return True

    @staticmethod
    async def disable(id: int) -> bool:
        """禁用工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflowModel).where(UserWorkflowModel.id == id))
            model = result.first()
            if not model:
                return False
            model.is_enabled = False
            model.updated_at = datetime.now()
            await session.commit()
            return True

    @staticmethod
    async def duplicate(id: int) -> Optional[UserWorkflowModel]:
        """复制工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflowModel).where(UserWorkflowModel.id == id))
            original = result.first()
            if not original:
                return None

            new_workflow_id = str(uuid.uuid4())
            new_model = UserWorkflowModel(
                workflow_id=new_workflow_id,
                name=f"{original.name} (副本)",
                description=original.description,
                mid=original.mid,
                on_error=original.on_error,
            )
            new_model.set_steps(original.get_steps())
            new_model.set_tags(original.get_tags())

            session.add(new_model)
            await session.commit()
            await session.refresh(new_model)
            return new_model


class ExecutionLogCrudService:
    """执行日志 CRUD 服务"""

    @staticmethod
    async def create(
        workflow_id: str,
        session_id: str,
        browser_id: str,
        mid: str,
        execution_id: str,
        status: str,
        results: List[Dict[str, Any]],
    ) -> WorkflowExecutionLogModel:
        """创建执行日志"""
        async with DatabaseSessionManager.async_session() as session:
            success_count = sum(1 for r in results if r.get("success"))
            failed_count = len(results) - success_count

            model = WorkflowExecutionLogModel(
                workflow_id=workflow_id,
                session_id=session_id,
                browser_id=browser_id,
                mid=mid,
                execution_id=execution_id,
                status=status,
                steps_count=len(results),
                success_count=success_count,
                failed_count=failed_count,
            )
            model.set_results(results)
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def update_status(
        execution_id: str,
        status: str,
        total_time: float,
        results: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """更新执行状态"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLogModel)
                .where(WorkflowExecutionLogModel.execution_id == execution_id)
            )
            model = result.first()
            if not model:
                return False

            model.status = status
            model.total_time = total_time
            if results is not None:
                model.set_results(results)
                model.success_count = sum(1 for r in results if r.get("success"))
                model.failed_count = len(results) - model.success_count

            if status in ("success", "failed", "timeout", "cancelled"):
                model.finished_at = datetime.now()

            await session.commit()
            return True

    @staticmethod
    async def get_by_execution_id(execution_id: str) -> Optional[WorkflowExecutionLogModel]:
        """根据执行ID获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLogModel)
                .where(WorkflowExecutionLogModel.execution_id == execution_id)
            )
            return result.first()

    @staticmethod
    async def list_by_workflow(
        workflow_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> List[WorkflowExecutionLogModel]:
        """获取工作流的所有执行记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLogModel)
                .where(WorkflowExecutionLogModel.workflow_id == workflow_id)
                .order_by(WorkflowExecutionLogModel.started_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def list_by_user(
        mid: str,
        skip: int = 0,
        limit: int = 100,
    ) -> List[WorkflowExecutionLogModel]:
        """获取用户的所有执行记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLogModel)
                .where(WorkflowExecutionLogModel.mid == mid)
                .order_by(WorkflowExecutionLogModel.started_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def delete_old(days: int = 30) -> int:
        """删除旧日志"""
        async with DatabaseSessionManager.async_session() as session:
            cutoff = datetime.now() - timedelta(days=days)
            result = await session.exec(
                select(WorkflowExecutionLogModel)
                .where(WorkflowExecutionLogModel.started_at < cutoff)
            )
            models = result.all()
            count = len(models)
            for model in models:
                await session.delete(model)
            await session.commit()
            return count


# 全局服务实例
action_crud = ActionCrudService()
workflow_crud = WorkflowCrudService()
execution_log_crud = ExecutionLogCrudService()
