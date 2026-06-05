"""
操作 CRUD 服务
"""
from typing import Any, Dict, List
from datetime import datetime
import uuid
from sqlmodel import select, update

from app.models.database.workflow.models import CompositeActionModel, BuiltinActionType
from app.models.execution.action_params import BaseWorkflowStep
from app.models.exceptions.base_exception import NameAlreadyExistsException
from app.utils.depends.session_manager import DatabaseSessionManager


class ActionCrudService:
    """操作 CRUD 服务"""

    @staticmethod
    async def create(
        mid: int,
        action_id: str,
        name: str,
        action_type: BuiltinActionType = BuiltinActionType.COMPOSITE,
        parameters_schema: List[Dict[str, Any]] = None,
        steps: List[Dict[str, Any] | BaseWorkflowStep] | None = None,
        is_composite: bool = False,
        description: str = "",
        tags: list[str] | None = None,
        input_vars: list[dict[str, Any]] | None = None,
        output_vars: list[str] | None = None,
        is_public: bool = False,
        timeout: int = 30000,
        retry_on_error: bool = False,
        retry_times: int = 0,
        retry_delay: float = 1.0,
    ) -> CompositeActionModel:
        # 为每个 step dict 添加 action_type（从 action_id 推断），确保 discriminated union 能匹配
        # 同时将 Pydantic 模型实例转为 dict，避免 JSON 序列化错误
        def _normalize_step(s: Dict[str, Any] | BaseWorkflowStep) -> dict:
            if not isinstance(s, dict):
                if hasattr(s, 'model_dump'):
                    s = s.model_dump(exclude_none=True, mode='json')
                else:
                    s = dict(s) if s else {}
            if 'action_type' not in s and 'action_id' in s:
                if s['action_id'].startswith('ca_'):
                    s['action_type'] = BuiltinActionType.COMPOSITE.value
                else:
                    s['action_type'] = s['action_id']
            if 'children' in s and s['children']:
                s['children'] = [_normalize_step(c) for c in s['children']]
            return s

        normalized_steps = [_normalize_step(s) for s in (steps or [])]

        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(CompositeActionModel).where(
                    (CompositeActionModel.mid == mid) & (CompositeActionModel.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="操作")

            model = CompositeActionModel(
                action_id=action_id,
                name=name,
                action_type=action_type,
                description=description,
                mid=mid,
                original_mid=mid,
                timeout=timeout,
                retry_on_error=retry_on_error,
                retry_times=retry_times,
                retry_delay=retry_delay,
                is_composite=is_composite,
                parameters_schema=parameters_schema or [],
                steps=normalized_steps,
                tags=tags or [],
                input_vars=input_vars or [],
                output_vars=output_vars or [],
                is_public=is_public,
            )
            session.add(model)

            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def get_by_id(id: int) -> CompositeActionModel | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CompositeActionModel).where(CompositeActionModel.id == id))
            return result.first()

    @staticmethod
    async def get_by_action_id(action_id: str) -> CompositeActionModel | None:
        """通过 action_id 字符串查找操作"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel).where(CompositeActionModel.action_id == action_id)
            )
            return result.first()

    @staticmethod
    async def count_by_user(mid: int, filter_type: str = "all") -> int:
        from sqlmodel import func

        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(CompositeActionModel.id))
            if filter_type == "private":
                query = query.where((CompositeActionModel.mid == mid) & (CompositeActionModel.is_public == False))
            elif filter_type == "public":
                query = query.where((CompositeActionModel.mid == mid) & (CompositeActionModel.is_public == True))
            elif filter_type == "community":
                query = query.where((CompositeActionModel.mid != mid) & (CompositeActionModel.is_public == True))
            elif filter_type == "verified":
                query = query.where(CompositeActionModel.is_verified == True)
            else:
                query = query.where((CompositeActionModel.mid == mid) | (CompositeActionModel.is_public == True))

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
    ) -> List[CompositeActionModel]:
        from sqlmodel import col

        async with DatabaseSessionManager.async_session() as session:
            query = select(CompositeActionModel)
            if filter_type == "private":
                query = query.where((CompositeActionModel.mid == mid) & (CompositeActionModel.is_public == False))
            elif filter_type == "public":
                query = query.where((CompositeActionModel.mid == mid) & (CompositeActionModel.is_public == True))
            elif filter_type == "community":
                query = query.where((CompositeActionModel.mid != mid) & (CompositeActionModel.is_public == True))
            elif filter_type == "verified":
                query = query.where(CompositeActionModel.is_verified == True)
            else:
                query = query.where((CompositeActionModel.mid == mid) | (CompositeActionModel.is_public == True))

            sort_field = getattr(CompositeActionModel, sort_by, CompositeActionModel.updated_at)
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
        parameters_schema: List[Dict[str, Any]] | None = None,
        steps: List[Dict[str, Any]] | None = None,
        tags: List[str] | None = None,
        input_vars: List[Dict[str, Any]] | None = None,
        output_vars: List[str] | None = None,
        is_composite: bool | None = None,
        timeout: int | None = None,
        retry_on_error: bool | None = None,
        retry_times: int | None = None,
        retry_delay: float | None = None,
        is_public: bool | None = None,
    ) -> CompositeActionModel | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CompositeActionModel).where(CompositeActionModel.id == id))
            model = result.first()
            if not model:
                return None

            if name is not None and name != model.name:
                existing = await session.exec(
                    select(CompositeActionModel).where(
                        (CompositeActionModel.mid == model.mid) &
                        (CompositeActionModel.name == name) &
                        (CompositeActionModel.id != id)
                    )
                )
                if existing.first():
                    raise NameAlreadyExistsException(name=name, name_type="操作")
                model.name = name

            if description is not None:
                model.description = description
            if parameters_schema is not None:
                model.parameters_schema = parameters_schema
            if steps is not None:
                model.steps = steps
            if tags is not None:
                model.tags = tags
            if input_vars is not None:
                model.input_vars = input_vars
            if output_vars is not None:
                model.output_vars = output_vars
            if is_composite is not None:
                model.is_composite = is_composite
            if timeout is not None:
                model.timeout = timeout
            if retry_on_error is not None:
                model.retry_on_error = retry_on_error
            if retry_times is not None:
                model.retry_times = retry_times
            if retry_delay is not None:
                model.retry_delay = retry_delay
            if is_public is not None:
                model.is_public = is_public

            model.updated_at = datetime.now()
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def delete(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CompositeActionModel).where(CompositeActionModel.id == id))
            model = result.first()
            if not model:
                return False

            if model.forked_from_id:
                await session.exec(
                    update(CompositeActionModel)
                    .where(CompositeActionModel.id == model.forked_from_id)
                    .values(forks_count=CompositeActionModel.forks_count - 1)
                )

            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CompositeActionModel).where(CompositeActionModel.id == id))
            model = result.first()
            if not model:
                return False
            model.is_enabled = True
            model.updated_at = datetime.now()
            await session.commit()
            return True

    @staticmethod
    async def disable(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CompositeActionModel).where(CompositeActionModel.id == id))
            model = result.first()
            if not model:
                return False
            model.is_enabled = False
            model.updated_at = datetime.now()
            await session.commit()
            return True

    @staticmethod
    async def increment_likes(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            stmt = update(CompositeActionModel).where(CompositeActionModel.id == id).values(likes_count=CompositeActionModel.likes_count + 1)
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            stmt = update(CompositeActionModel).where(CompositeActionModel.id == id).values(reports_count=CompositeActionModel.reports_count + 1)
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def list_forks(action_id: int, skip: int = 0, limit: int = 50) -> List[CompositeActionModel]:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel)
                .where(CompositeActionModel.forked_from_id == action_id)
                .order_by(CompositeActionModel.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def fork(id: int, target_mid: int, new_name: str | None = None) -> CompositeActionModel | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CompositeActionModel).where(CompositeActionModel.id == id))
            original = result.first()
            if not original:
                return None

            if not original.is_public:
                raise ValueError("只能 Fork 公开的操作")

            new_action_id = f"ca_{uuid.uuid4().hex[:12]}"
            if not new_name:
                new_name = f"{original.name} (Fork)"

            existing = await session.exec(
                select(CompositeActionModel).where(
                    (CompositeActionModel.mid == target_mid) & (CompositeActionModel.name == new_name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=new_name, name_type="操作")

            new_model = CompositeActionModel(
                action_id=new_action_id,
                name=new_name,
                action_type=original.action_type,
                description=f"Forked from: {original.name}",
                mid=target_mid,
                original_mid=original.original_mid,
                timeout=original.timeout,
                is_composite=original.is_composite,
                parameters_schema=original.parameters_schema.copy() if original.parameters_schema else [],
                steps=original.steps.copy() if original.steps else [],
                tags=original.tags.copy() if original.tags else [],
                input_vars=original.input_vars.copy() if original.input_vars else [],
                output_vars=original.output_vars.copy() if original.output_vars else [],
                is_public=False,
                forked_from_id=original.id,
            )

            session.add(new_model)
            await session.exec(
                update(CompositeActionModel).where(CompositeActionModel.id == original.id).values(forks_count=CompositeActionModel.forks_count + 1)
            )

            await session.commit()
            await session.refresh(new_model)
            return new_model


action_crud = ActionCrudService()