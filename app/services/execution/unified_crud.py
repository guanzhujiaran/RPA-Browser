"""
Unified CRUD Service - 统一 CRUD 服务

支持 ExecutionRecord 和 WorkflowRecord 的 CRUD 操作。
"""

import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

from sqlmodel import select, func

from app.models.database.workflow.unified_models import (
    ExecutionRecord,
    WorkflowRecord,
    ActionCategory,
    ActionExecutionLog,
    WorkflowExecutionSession,
    TriggerType,
    ExecutionStatus,
)
from app.utils.depends.session_manager import DatabaseSessionManager
from app.models.exceptions.base_exception import NameAlreadyExistsException


class UnifiedCRUD:
    """
    统一 CRUD 服务

    处理 ExecutionRecord 和 WorkflowRecord 的增删改查。
    """

    async def create_execution_record(
        self,
        action_id: str,
        name: str,
        category: ActionCategory,
        mid: int,
        description: str = "",
        entry_action_id: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        hook_type: Optional[str] = None,
        target_action_id: Optional[str] = None,
        parameters_schema: Optional[List[Dict[str, Any]]] = None,
        tags: Optional[List[str]] = None,
        is_public: bool = False,
    ) -> ExecutionRecord:
        """
        创建统一动作记录

        Args:
            action_id: 动作 ID
            name: 名称
            category: 类别
            mid: 用户 ID
            description: 描述
            entry_action_id: 入口动作 ID（组合动作）
            steps: 步骤列表
            hook_type: 钩子类型（插件）
            target_action_id: 目标动作 ID（插件）
            parameters_schema: 参数定义
            tags: 标签
            is_public: 是否公开

        Returns:
            ExecutionRecord: 创建的记录
        """
        async with DatabaseSessionManager.async_session() as session:
            # 检查名称唯一性
            result = await session.exec(
                select(ExecutionRecord).where(
                    ExecutionRecord.mid == mid,
                    ExecutionRecord.name == name,
                )
            )
            if result.first():
                raise NameAlreadyExistsException(f"名称 '{name}' 已存在")

            record = ExecutionRecord(
                action_id=action_id,
                name=name,
                category=category,
                description=description,
                entry_action_id=entry_action_id,
                steps=steps or [],
                hook_type=hook_type,
                target_action_id=target_action_id,
                parameters_schema=parameters_schema or [],
                tags=tags or [],
                mid=mid,
                is_public=is_public,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)

            return record

    async def get_execution_record(self, action_id: str) -> Optional[ExecutionRecord]:
        """获取动作记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ExecutionRecord).where(ExecutionRecord.action_id == action_id)
            )
            return result.first()

    async def get_execution_record_by_id(self, record_id: int) -> Optional[ExecutionRecord]:
        """根据 ID 获取动作记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ExecutionRecord).where(ExecutionRecord.id == record_id)
            )
            return result.first()

    async def update_execution_record(
        self,
        record_id: int,
        mid: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        parameters_schema: Optional[List[Dict[str, Any]]] = None,
        tags: Optional[List[str]] = None,
        is_enabled: Optional[bool] = None,
        is_public: Optional[bool] = None,
        timeout: Optional[int] = None,
    ) -> Optional[ExecutionRecord]:
        """更新动作记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ExecutionRecord).where(
                    ExecutionRecord.id == record_id,
                    ExecutionRecord.mid == mid,
                )
            )
            record = result.first()

            if not record:
                return None

            if name is not None:
                record.name = name
            if description is not None:
                record.description = description
            if steps is not None:
                record.steps = steps
            if parameters_schema is not None:
                record.parameters_schema = parameters_schema
            if tags is not None:
                record.tags = tags
            if is_enabled is not None:
                record.is_enabled = is_enabled
            if is_public is not None:
                record.is_public = is_public
            if timeout is not None:
                record.timeout = timeout

            record.updated_at = datetime.now()
            await session.commit()
            await session.refresh(record)

            return record

    async def delete_execution_record(self, record_id: int, mid: int) -> bool:
        """删除动作记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ExecutionRecord).where(
                    ExecutionRecord.id == record_id,
                    ExecutionRecord.mid == mid,
                )
            )
            record = result.first()

            if not record:
                return False

            await session.delete(record)
            await session.commit()

            return True

    async def list_execution_records(
        self,
        mid: int,
        skip: int = 0,
        limit: int = 20,
        category: Optional[ActionCategory] = None,
        is_public: Optional[bool] = None,
    ) -> tuple[List[ExecutionRecord], int]:
        """
        列出动作记录

        Returns:
            (记录列表, 总数)
        """
        async with DatabaseSessionManager.async_session() as session:
            query = select(ExecutionRecord).where(ExecutionRecord.mid == mid)

            if category:
                query = query.where(ExecutionRecord.category == category)
            if is_public is not None:
                query = query.where(ExecutionRecord.is_public == is_public)

            # 计数
            count_result = await session.exec(
                select(func.count()).select_from(query.subquery())
            )
            total = count_result.one()

            # 分页
            query = query.offset(skip).limit(limit).order_by(ExecutionRecord.updated_at.desc())
            result = await session.exec(query)
            records = result.all()

            return list(records), total

    async def list_public_execution_records(
        self,
        skip: int = 0,
        limit: int = 20,
        category: Optional[ActionCategory] = None,
    ) -> tuple[List[ExecutionRecord], int]:
        """列出公开的动作记录"""
        async with DatabaseSessionManager.async_session() as session:
            query = select(ExecutionRecord).where(ExecutionRecord.is_public == True)

            if category:
                query = query.where(ExecutionRecord.category == category)

            count_result = await session.exec(
                select(func.count()).select_from(query.subquery())
            )
            total = count_result.one()

            query = query.offset(skip).limit(limit).order_by(ExecutionRecord.likes_count.desc())
            result = await session.exec(query)

            return list(result.all()), total

    async def fork_execution_record(
        self,
        record_id: int,
        target_mid: int,
        new_name: Optional[str] = None,
    ) -> Optional[ExecutionRecord]:
        """Fork 动作记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ExecutionRecord).where(ExecutionRecord.id == record_id)
            )
            original = result.first()

            if not original:
                return None

            if not original.is_public and original.mid != target_mid:
                raise ValueError("只能 Fork 公开的动作")

            # 检查目标用户是否有同名动作
            target_name = new_name or original.name
            check_result = await session.exec(
                select(ExecutionRecord).where(
                    ExecutionRecord.mid == target_mid,
                    ExecutionRecord.name == target_name,
                )
            )
            if check_result.first():
                raise NameAlreadyExistsException(f"名称 '{target_name}' 已存在")

            # 创建 Fork
            new_action_id = f"ca_{uuid.uuid4().hex[:12]}"
            forked = ExecutionRecord(
                action_id=new_action_id,
                name=target_name,
                category=original.category,
                description=original.description,
                entry_action_id=original.entry_action_id,
                steps=original.steps,
                hook_type=original.hook_type,
                target_action_id=original.target_action_id,
                parameters_schema=original.parameters_schema,
                tags=original.tags,
                mid=target_mid,
                is_enabled=True,
                is_public=False,
                timeout=original.timeout,
                forked_from_id=original.id,
            )
            session.add(forked)

            # 增加原记录的 fork 计数
            original.forks_count += 1

            await session.commit()
            await session.refresh(forked)

            return forked

    # ==================== Workflow CRUD ====================

    async def create_workflow(
        self,
        name: str,
        entry_action_id: str,
        mid: int,
        description: str = "",
        params_template: Optional[Dict[str, Any]] = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        crontab_expression: Optional[str] = None,
        is_scheduled: bool = False,
        tags: Optional[List[str]] = None,
        is_public: bool = False,
        on_error: str = "stop",
    ) -> WorkflowRecord:
        """创建工作流"""
        async with DatabaseSessionManager.async_session() as session:
            # 检查名称唯一性
            result = await session.exec(
                select(WorkflowRecord).where(
                    WorkflowRecord.mid == mid,
                    WorkflowRecord.name == name,
                )
            )
            if result.first():
                raise NameAlreadyExistsException(f"名称 '{name}' 已存在")

            workflow_id = f"wf_{uuid.uuid4().hex[:12]}"

            workflow = WorkflowRecord(
                workflow_id=workflow_id,
                name=name,
                description=description,
                entry_action_id=entry_action_id,
                params_template=params_template or {},
                trigger_type=trigger_type,
                crontab_expression=crontab_expression,
                is_scheduled=is_scheduled,
                tags=tags or [],
                mid=mid,
                is_public=is_public,
                on_error=on_error,
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)

            # 如果启用调度，添加到调度器
            if is_scheduled and crontab_expression:
                from app.services.execution.workflow_scheduler import workflow_scheduler

                workflow_scheduler.add_workflow_schedule(
                    workflow_id=workflow.workflow_id,
                    mid=mid,
                    crontab_expression=crontab_expression,
                )

            return workflow

    async def get_workflow(self, workflow_id: str) -> Optional[WorkflowRecord]:
        """获取工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRecord).where(WorkflowRecord.workflow_id == workflow_id)
            )
            return result.first()

    async def get_workflow_by_id(self, record_id: int) -> Optional[WorkflowRecord]:
        """根据 ID 获取工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRecord).where(WorkflowRecord.id == record_id)
            )
            return result.first()

    async def update_workflow(
        self,
        record_id: int,
        mid: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        entry_action_id: Optional[str] = None,
        params_template: Optional[Dict[str, Any]] = None,
        trigger_type: Optional[TriggerType] = None,
        crontab_expression: Optional[str] = None,
        is_scheduled: Optional[bool] = None,
        tags: Optional[List[str]] = None,
        is_enabled: Optional[bool] = None,
        is_public: Optional[bool] = None,
        on_error: Optional[str] = None,
        max_retries: Optional[int] = None,
    ) -> Optional[WorkflowRecord]:
        """更新工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRecord).where(
                    WorkflowRecord.id == record_id,
                    WorkflowRecord.mid == mid,
                )
            )
            workflow = result.first()

            if not workflow:
                return None

            if name is not None:
                workflow.name = name
            if description is not None:
                workflow.description = description
            if entry_action_id is not None:
                workflow.entry_action_id = entry_action_id
            if params_template is not None:
                workflow.params_template = params_template
            if trigger_type is not None:
                workflow.trigger_type = trigger_type
            if crontab_expression is not None:
                workflow.crontab_expression = crontab_expression
            if is_scheduled is not None:
                workflow.is_scheduled = is_scheduled
            if tags is not None:
                workflow.tags = tags
            if is_enabled is not None:
                workflow.is_enabled = is_enabled
            if is_public is not None:
                workflow.is_public = is_public
            if on_error is not None:
                workflow.on_error = on_error
            if max_retries is not None:
                workflow.max_retries = max_retries

            workflow.updated_at = datetime.now()
            await session.commit()
            await session.refresh(workflow)

            # 更新调度器
            from app.services.execution.workflow_scheduler import workflow_scheduler

            if is_scheduled and crontab_expression:
                workflow_scheduler.add_workflow_schedule(
                    workflow_id=workflow.workflow_id,
                    mid=mid,
                    crontab_expression=crontab_expression,
                )
            else:
                workflow_scheduler.remove_workflow_schedule(workflow.workflow_id)

            return workflow

    async def delete_workflow(self, record_id: int, mid: int) -> bool:
        """删除工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRecord).where(
                    WorkflowRecord.id == record_id,
                    WorkflowRecord.mid == mid,
                )
            )
            workflow = result.first()

            if not workflow:
                return False

            # 从调度器移除
            from app.services.execution.workflow_scheduler import workflow_scheduler

            workflow_scheduler.remove_workflow_schedule(workflow.workflow_id)

            await session.delete(workflow)
            await session.commit()

            return True

    async def list_workflows(
        self,
        mid: int,
        skip: int = 0,
        limit: int = 20,
        is_public: Optional[bool] = None,
    ) -> tuple[List[WorkflowRecord], int]:
        """列出用户工作流"""
        async with DatabaseSessionManager.async_session() as session:
            query = select(WorkflowRecord).where(WorkflowRecord.mid == mid)

            if is_public is not None:
                query = query.where(WorkflowRecord.is_public == is_public)

            count_result = await session.exec(
                select(func.count()).select_from(query.subquery())
            )
            total = count_result.one()

            query = query.offset(skip).limit(limit).order_by(WorkflowRecord.updated_at.desc())
            result = await session.exec(query)

            return list(result.all()), total

    async def fork_workflow(
        self,
        record_id: int,
        target_mid: int,
        new_name: Optional[str] = None,
    ) -> Optional[WorkflowRecord]:
        """Fork 工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRecord).where(WorkflowRecord.id == record_id)
            )
            original = result.first()

            if not original:
                return None

            if not original.is_public and original.mid != target_mid:
                raise ValueError("只能 Fork 公开的工作流")

            target_name = new_name or original.name
            check_result = await session.exec(
                select(WorkflowRecord).where(
                    WorkflowRecord.mid == target_mid,
                    WorkflowRecord.name == target_name,
                )
            )
            if check_result.first():
                raise NameAlreadyExistsException(f"名称 '{target_name}' 已存在")

            new_workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
            forked = WorkflowRecord(
                workflow_id=new_workflow_id,
                name=target_name,
                description=original.description,
                entry_action_id=original.entry_action_id,
                params_template=original.params_template,
                trigger_type=TriggerType.MANUAL,
                is_scheduled=False,
                tags=original.tags,
                mid=target_mid,
                is_enabled=True,
                is_public=False,
                on_error=original.on_error,
                forked_from_id=original.id,
            )
            session.add(forked)

            original.forks_count += 1

            await session.commit()
            await session.refresh(forked)

            return forked


unified_crud = UnifiedCRUD()
