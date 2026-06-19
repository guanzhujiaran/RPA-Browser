"""
工作流 CRUD 服务
"""
from typing import Any, Dict, List
from datetime import datetime
import uuid
from sqlmodel import select, update
from sqlalchemy import true, false

from app.models.database.workflow.models import UserWorkflow, WorkflowPluginRelation, CompositeActionModel, UserPlugin
from app.models.execution.action_params import PluginConfig
from app.models.exceptions.base_exception import NameAlreadyExistsException
from app.utils.depends.session_manager import DatabaseSessionManager


class WorkflowCrudService:
    """工作流 CRUD 服务"""

    @staticmethod
    async def create(
        mid: int,
        workflow_id: str,
        name: str,
        custom_action_id: str,
        description: str = "",
        trigger_type: str = "manual",
        trigger_config: Dict | None = None,
        is_public: bool = False,
        enabled_plugins: List[PluginConfig] | None = None,
    ) -> UserWorkflow:
        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(UserWorkflow).where(
                    (UserWorkflow.mid == mid) & (UserWorkflow.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="工作流")

            model = UserWorkflow(
                workflow_id=workflow_id,
                name=name,
                custom_action_id=custom_action_id,
                description=description,
                mid=mid,
                original_mid=mid,
                trigger_type=trigger_type,
                trigger_config=trigger_config or {},
                is_public=is_public,
            )
            session.add(model)

            if enabled_plugins:
                for plugin_config in enabled_plugins:
                    link = WorkflowPluginRelation(
                        workflow_id=workflow_id,
                        plugin_id=plugin_config.plugin_id,
                        config_params=plugin_config.config_params
                    )
                    session.add(link)

            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def get_by_id(id: int) -> UserWorkflow | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            return result.first()

    @staticmethod
    async def get_by_workflow_id(workflow_id: str) -> UserWorkflow | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow).where(UserWorkflow.workflow_id == workflow_id)
            )
            return result.first()

    @staticmethod
    async def get_enabled_plugins(workflow_id: str) -> List[PluginConfig]:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowPluginRelation).where(WorkflowPluginRelation.workflow_id == workflow_id)
            )
            links = result.all()
            
            plugins: list[PluginConfig] = []
            for link in links:
                plugin_info = await session.exec(
                    select(UserPlugin).where(UserPlugin.plugin_id == link.plugin_id)
                )
                plugin = plugin_info.first()
                if plugin:
                    plugins.append(PluginConfig(
                        plugin_id=link.plugin_id,
                        config_params=link.config_params or {},
                        hook_type=plugin.hook_type,
                        priority=plugin.priority,
                    ))
            
            # 按优先级排序
            plugins.sort(key=lambda x: x.priority)
            return plugins

    @staticmethod
    async def count_by_user(mid: int, filter_type: str = "all") -> int:
        from sqlmodel import func

        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(UserWorkflow.id))
            if filter_type == "private":
                query = query.where((UserWorkflow.mid == str(mid)) & (UserWorkflow.is_public == false()))
            elif filter_type == "public":
                query = query.where(UserWorkflow.is_public == true())
            elif filter_type == "community":
                query = query.where((UserWorkflow.mid != str(mid)) & (UserWorkflow.is_public == true()))
            elif filter_type == "verified":
                query = query.where(UserWorkflow.is_verified == true())
            else:
                query = query.where((UserWorkflow.mid == str(mid)) | (UserWorkflow.is_public == true()))

            result = await session.exec(query)
            return result.one()

    @staticmethod
    async def list_by_user(
        mid: int,
        skip: int = 0,
        limit: int = 100,
        filter_type: str = "all",
        sort_by: str = "updated_at",
        sort_order: str = "desc"
    ) -> List[UserWorkflow]:
        from sqlmodel import col

        async with DatabaseSessionManager.async_session() as session:
            query = select(UserWorkflow)
            if filter_type == "private":
                query = query.where((UserWorkflow.mid == str(mid)) & (UserWorkflow.is_public == false()))
            elif filter_type == "public":
                query = query.where(UserWorkflow.is_public == true())
            elif filter_type == "community":
                query = query.where((UserWorkflow.mid != str(mid)) & (UserWorkflow.is_public == true()))
            elif filter_type == "verified":
                query = query.where(UserWorkflow.is_verified == true())
            else:
                query = query.where((UserWorkflow.mid == str(mid)) | (UserWorkflow.is_public == true()))

            sort_field = getattr(UserWorkflow, sort_by, UserWorkflow.updated_at)
            if sort_order == "asc":
                query = query.order_by(col(sort_field).asc())
            else:
                query = query.order_by(col(sort_field).desc())

            query = query.offset(skip).limit(limit)
            result = await session.exec(query)
            return result.all()

    @staticmethod
    async def update(
        id: int,
        name: str | None = None,
        description: str | None = None,
        custom_action_id: str | None = None,
        trigger_type: str | None = None,
        trigger_config: Dict | None = None,
        is_enabled: bool | None = None,
        is_public: bool | None = None,
        enabled_plugins: List[PluginConfig] | None = None,
    ) -> UserWorkflow | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            model = result.first()
            if not model:
                return None

            if name is not None and name != model.name:
                existing = await session.exec(
                    select(UserWorkflow).where(
                        (UserWorkflow.mid == model.mid) &
                        (UserWorkflow.name == name) &
                        (UserWorkflow.id != id)
                    )
                )
                if existing.first():
                    raise NameAlreadyExistsException(name=name, name_type="工作流")
                model.name = name

            if description is not None:
                model.description = description
            if custom_action_id is not None:
                model.custom_action_id = custom_action_id
            if trigger_type is not None:
                model.trigger_type = trigger_type
            if trigger_config is not None:
                model.trigger_config = trigger_config
            if is_enabled is not None:
                model.is_enabled = is_enabled
            if is_public is not None:
                model.is_public = is_public

            if enabled_plugins is not None:
                old_links = await session.exec(
                    select(WorkflowPluginRelation).where(WorkflowPluginRelation.workflow_id == model.workflow_id)
                )
                for link in old_links.all():
                    await session.delete(link)

                for plugin_config in enabled_plugins:
                    link = WorkflowPluginRelation(
                        workflow_id=model.workflow_id,
                        plugin_id=plugin_config.plugin_id,
                        config_params=plugin_config.config_params
                    )
                    session.add(link)

            model.updated_at = datetime.now()
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def delete(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            model = result.first()
            if not model:
                return False

            if model.forked_from_id:
                await session.exec(
                    update(UserWorkflow)
                    .where(UserWorkflow.id == model.forked_from_id)
                    .values(forks_count=UserWorkflow.forks_count - 1)
                )

            links = await session.exec(
                select(WorkflowPluginRelation).where(WorkflowPluginRelation.workflow_id == model.workflow_id)
            )
            for link in links.all():
                await session.delete(link)

            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserWorkflow).where(UserWorkflow.id == id).values(is_enabled=True, updated_at=datetime.now())
            )
            await session.commit()
            return True

    @staticmethod
    async def disable(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserWorkflow).where(UserWorkflow.id == id).values(is_enabled=False, updated_at=datetime.now())
            )
            await session.commit()
            return True

    @staticmethod
    async def duplicate(id: int) -> UserWorkflow | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            original = result.first()
            if not original:
                return None

            new_workflow_id = str(uuid.uuid4())
            new_model = UserWorkflow(
                workflow_id=new_workflow_id,
                name=f"{original.name} (副本)",
                description=original.description,
                mid=original.mid,
                original_mid=original.original_mid,
                custom_action_id=original.custom_action_id,
                trigger_type=original.trigger_type,
                trigger_config=original.trigger_config,
                is_public=False,
            )

            session.add(new_model)
            await session.commit()
            await session.refresh(new_model)
            return new_model

    @staticmethod
    async def fork(id: int, target_mid: int, new_name: str | None = None) -> UserWorkflow | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            original = result.first()
            if not original:
                return None

            if not original.is_public:
                raise ValueError("只能 Fork 公开的工作流")

            new_workflow_id = str(uuid.uuid4())
            if not new_name:
                new_name = f"{original.name} (Fork)"

            existing = await session.exec(
                select(UserWorkflow).where(
                    (UserWorkflow.mid == target_mid) & (UserWorkflow.name == new_name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=new_name, name_type="工作流")

            new_model = UserWorkflow(
                workflow_id=new_workflow_id,
                name=new_name,
                description=f"Forked from: {original.name}",
                mid=target_mid,
                original_mid=original.original_mid,
                custom_action_id=original.custom_action_id,
                trigger_type=original.trigger_type,
                trigger_config=original.trigger_config,
                is_public=False,
                forked_from_id=original.id,
            )

            session.add(new_model)
            await session.exec(
                update(UserWorkflow)
                .where(UserWorkflow.id == original.id)
                .values(forks_count=UserWorkflow.forks_count + 1)
            )

            await session.commit()
            await session.refresh(new_model)
            return new_model

    @staticmethod
    async def increment_likes(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserWorkflow).where(UserWorkflow.id == id).values(likes_count=UserWorkflow.likes_count + 1)
            )
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserWorkflow).where(UserWorkflow.id == id).values(reports_count=UserWorkflow.reports_count + 1)
            )
            await session.commit()
            return True

    @staticmethod
    async def list_forks(workflow_id: int, skip: int = 0, limit: int = 50) -> List[UserWorkflow]:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow)
                .where(UserWorkflow.forked_from_id == workflow_id)
                .order_by(UserWorkflow.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()


workflow_crud_svr = WorkflowCrudService()
