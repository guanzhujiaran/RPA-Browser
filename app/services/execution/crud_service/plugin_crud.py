"""
插件 CRUD 服务
"""
from typing import List
from datetime import datetime
import uuid
from sqlmodel import select, update

from app.models.database.workflow.models import CompositeAction, UserPlugin
from app.models.exceptions.base_exception import NameAlreadyExistsException
from app.utils.depends.session_manager import DatabaseSessionManager


class PluginCrudService:
    """插件 CRUD 服务"""

    @staticmethod
    async def create(
        mid: int,
        plugin_id: str,
        name: str,
        hook_type: str,
        custom_action_id: str,
        description: str = "",
        priority: int = 100,
        is_public: bool = False,
    ) -> UserPlugin:
        from app.models.database.workflow.models import PluginHookEnum

        async with DatabaseSessionManager.async_session() as session:
            valid_hook_types = [hook.value for hook in PluginHookEnum]
            if hook_type not in valid_hook_types:
                raise ValueError(f"无效的钩子类型 '{hook_type}'。有效的钩子类型包括: {', '.join(valid_hook_types)}")

            action_result = await session.exec(
                select(CompositeAction).where(
                    (CompositeAction.action_id == custom_action_id) &
                    (CompositeAction.is_enabled == True)
                )
            )
            action_model = action_result.first()
            if not action_model:
                raise ValueError(f"自定义动作 '{custom_action_id}' 不存在或已被禁用")

            if not action_model.is_public and str(action_model.mid) != str(mid):
                raise ValueError(f"无权使用私有的自定义动作 '{custom_action_id}'")

            existing = await session.exec(
                select(UserPlugin).where(
                    (UserPlugin.mid == mid) & (UserPlugin.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="插件")

            model = UserPlugin(
                mid=mid,
                original_mid=mid,
                plugin_id=plugin_id,
                name=name,
                hook_type=hook_type,
                custom_action_id=custom_action_id,
                description=description,
                priority=priority,
                is_public=is_public,
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def get_by_id(id: int) -> UserPlugin | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPlugin).where(UserPlugin.id == id))
            return result.first()

    @staticmethod
    async def get_by_plugin_id(plugin_id: str) -> UserPlugin | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserPlugin).where(UserPlugin.plugin_id == plugin_id)
            )
            return result.first()

    @staticmethod
    async def get_by_ids(ids: List[str]) -> List[UserPlugin]:
        if not ids:
            return []
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserPlugin)
                .where(UserPlugin.plugin_id.in_(ids))
                .where(UserPlugin.is_enabled == True)
            )
            return result.all()

    @staticmethod
    async def count_by_user(mid: int, filter_type: str = "all") -> int:
        from sqlmodel import func

        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(UserPlugin.id))
            if filter_type == "private":
                query = query.where((UserPlugin.mid == mid) & (UserPlugin.is_public == False))
            elif filter_type == "public":
                query = query.where((UserPlugin.mid == mid) & (UserPlugin.is_public == True))
            elif filter_type == "community":
                query = query.where((UserPlugin.mid != mid) & (UserPlugin.is_public == True))
            elif filter_type == "verified":
                query = query.where(UserPlugin.is_verified == True)
            else:
                query = query.where((UserPlugin.mid == mid) | (UserPlugin.is_public == True))

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
    ) -> List[UserPlugin]:
        from sqlmodel import col

        async with DatabaseSessionManager.async_session() as session:
            query = select(UserPlugin)
            if filter_type == "private":
                query = query.where((UserPlugin.mid == mid) & (UserPlugin.is_public == False))
            elif filter_type == "public":
                query = query.where((UserPlugin.mid == mid) & (UserPlugin.is_public == True))
            elif filter_type == "community":
                query = query.where((UserPlugin.mid != mid) & (UserPlugin.is_public == True))
            elif filter_type == "verified":
                query = query.where(UserPlugin.is_verified == True)
            else:
                query = query.where((UserPlugin.mid == mid) | (UserPlugin.is_public == True))

            sort_field = getattr(UserPlugin, sort_by, UserPlugin.updated_at)
            if sort_order == "asc":
                query = query.order_by(col(sort_field).asc())
            else:
                query = query.order_by(col(sort_field).desc())

            result = await session.exec(query.offset(skip).limit(limit))
            return result.all()

    @staticmethod
    async def update(
        id: int,
        name: str | None = None,
        description: str | None = None,
        hook_type: str | None = None,
        custom_action_id: str | None = None,
        priority: int | None = None,
        is_public: bool | None = None,
    ) -> UserPlugin | None:
        from app.models.database.workflow.models import PluginHookEnum

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPlugin).where(UserPlugin.id == id))
            model = result.first()
            if not model:
                return None

            if hook_type is not None and hook_type != model.hook_type:
                valid_hook_types = [hook.value for hook in PluginHookEnum]
                if hook_type not in valid_hook_types:
                    raise ValueError(f"无效的钩子类型 '{hook_type}'。有效的钩子类型包括: {', '.join(valid_hook_types)}")
                model.hook_type = hook_type

            if custom_action_id is not None and custom_action_id != model.custom_action_id:
                action_result = await session.exec(
                    select(CompositeAction).where(
                        (CompositeAction.action_id == custom_action_id) &
                        (CompositeAction.is_enabled == True)
                    )
                )
                action_model = action_result.first()
                if not action_model:
                    raise ValueError(f"自定义动作 '{custom_action_id}' 不存在或已被禁用")

                if not action_model.is_public and str(action_model.mid) != str(model.mid):
                    raise ValueError(f"无权使用私有的自定义动作 '{custom_action_id}'")

                model.custom_action_id = custom_action_id

            if name is not None and name != model.name:
                existing = await session.exec(
                    select(UserPlugin).where(
                        (UserPlugin.mid == model.mid) &
                        (UserPlugin.name == name) &
                        (UserPlugin.id != id)
                    )
                )
                if existing.first():
                    raise NameAlreadyExistsException(name=name, name_type="插件")
                model.name = name

            if description is not None:
                model.description = description
            if priority is not None:
                model.priority = priority
            if is_public is not None:
                model.is_public = is_public

            model.updated_at = datetime.now()
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def delete(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPlugin).where(UserPlugin.id == id))
            model = result.first()
            if not model:
                return False

            if model.forked_from_id:
                await session.exec(
                    update(UserPlugin)
                    .where(UserPlugin.id == model.forked_from_id)
                    .values(forks_count=UserPlugin.forks_count - 1)
                )

            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserPlugin).where(UserPlugin.id == id).values(is_enabled=True, updated_at=datetime.now())
            )
            await session.commit()
            return True

    @staticmethod
    async def disable(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserPlugin).where(UserPlugin.id == id).values(is_enabled=False, updated_at=datetime.now())
            )
            await session.commit()
            return True

    @staticmethod
    async def increment_likes(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserPlugin).where(UserPlugin.id == id).values(likes_count=UserPlugin.likes_count + 1)
            )
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserPlugin).where(UserPlugin.id == id).values(reports_count=UserPlugin.reports_count + 1)
            )
            await session.commit()
            return True

    @staticmethod
    async def list_forks(plugin_id: int, skip: int = 0, limit: int = 50) -> List[UserPlugin]:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserPlugin)
                .where(UserPlugin.forked_from_id == plugin_id)
                .order_by(UserPlugin.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def fork(id: int, target_mid: int, new_name: str | None = None) -> UserPlugin | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPlugin).where(UserPlugin.id == id))
            original = result.first()
            if not original:
                return None

            if not original.is_public:
                raise ValueError("只能 Fork 公开的插件")

            new_plugin_id = f"plugin_{uuid.uuid4().hex[:8]}"
            if not new_name:
                new_name = f"{original.name} (Fork)"

            existing = await session.exec(
                select(UserPlugin).where(
                    (UserPlugin.mid == target_mid) & (UserPlugin.name == new_name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=new_name, name_type="插件")

            new_model = UserPlugin(
                mid=target_mid,
                original_mid=original.original_mid,
                plugin_id=new_plugin_id,
                name=new_name,
                hook_type=original.hook_type,
                custom_action_id=original.custom_action_id,
                description=f"Forked from: {original.name}",
                priority=original.priority,
                is_public=False,
                forked_from_id=original.id,
            )

            session.add(new_model)
            await session.exec(
                update(UserPlugin)
                .where(UserPlugin.id == original.id)
                .values(forks_count=UserPlugin.forks_count + 1)
            )

            await session.commit()
            await session.refresh(new_model)
            return new_model


plugin_crud = PluginCrudService()
