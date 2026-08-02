"""
操作 CRUD 服务
"""
from sqlalchemy import or_, and_, true
from typing import Any, Dict, List
from datetime import datetime
import uuid
from sqlmodel import select, update, delete

from app.models.database.workflow.models import CompositeActionModel, BuiltinActionType, TagModel, CompositeActionTagLink
from app.models.execution.action_params import BaseWorkflowStep
from app.models.common.exceptions.base_exception import NameAlreadyExistsException
from app.utils.depends.session_manager import DatabaseSessionManager


class ActionCrudService:
    """操作 CRUD 服务"""

    # ═══════════════ steps 中引用的自定义操作校验与详情注解 ═══════════════

    @staticmethod
    def _collect_ca_action_ids(steps: list[Dict]) -> list[str]:
        """递归收集 steps 中所有 ca_ 开头的 action_id（含嵌套在 params 中的分支步骤）"""
        ids: list[str] = []
        for step in steps:
            action_id = step.get("action_id", "")
            if isinstance(action_id, str) and action_id.startswith("ca_"):
                ids.append(action_id)
            params = step.get("params")
            if isinstance(params, dict):
                for key in ("TrueBranch", "FalseBranch", "loopBranch"):
                    branch = params.get(key)
                    if isinstance(branch, list):
                        ids.extend(ActionCrudService._collect_ca_action_ids(branch))
        return ids

    @staticmethod
    async def get_validated_action_details(
        action_ids: List[str],
        mid: int | str,
    ) -> Dict[str, Dict]:
        """批量获取自定义操作的完整信息，并校验权限（仅返回自己或公开的）

        Args:
            action_ids: ca_ 前缀的 action_id 列表（会自动去重）
            mid: 当前用户 mid

        Returns:
            action_id → 可序列化的详情字典（不含 steps，避免无限递归）

        Raises:
            ActionNotFoundException: 引用的操作不存在
            ActionNotAccessibleException: 无权访问引用的操作
        """
        from app.models.common.exceptions.base_exception import (
            ActionNotFoundException,
            ActionNotAccessibleException,
        )

        if not action_ids:
            return {}

        deduped = list(set(action_ids))
        mid_str = str(mid)

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel).where(
                    CompositeActionModel.action_id.in_(deduped)
                )
            )
            models = result.all()
            model_map: Dict[str, CompositeActionModel] = {m.action_id: m for m in models}
            tags_map = await ActionCrudService._get_tags_for_actions(session, [m.id for m in models])

        detail_map: Dict[str, Dict] = {}
        for aid in deduped:
            model = model_map.get(aid)
            if not model:
                raise ActionNotFoundException(aid)
            if model.mid != mid_str and not model.is_public:
                raise ActionNotAccessibleException(aid)

            # 序列化详情（不含 steps，避免无限嵌套）
            detail_map[aid] = {
                "action_id": model.action_id,
                "name": model.name,
                "version": model.version,
                "action_type": str(model.action_type.value) if hasattr(model.action_type, "value") else str(model.action_type),
                "description": model.description or "",
                "mid": model.mid,
                "tags": tags_map.get(model.id, []),
                "input_vars": list(model.input_vars or []),
                "output_vars": list(model.output_vars or []),
                "is_enabled": model.is_enabled,
                "is_public": model.is_public,
                "timeout": model.timeout,
                "retry_on_error": model.retry_on_error,
                "retry_times": model.retry_times,
                "retry_delay": model.retry_delay,
                "likes_count": model.likes_count or 0,
                "is_verified": model.is_verified or False,
                "forks_count": model.forks_count or 0,
                "forked_from_id": model.forked_from_id,
            }
        return detail_map

    @staticmethod
    def _annotate_steps_with_action_details(
        steps: list[Dict],
        detail_map: Dict[str, Dict],
    ) -> None:
        """递归为 steps 中 ca_ 开头的 action_id 添加 action_detail 字段"""
        for step in steps:
            action_id = step.get("action_id", "")
            if isinstance(action_id, str) and action_id.startswith("ca_"):
                step["action_detail"] = detail_map.get(action_id)
            params = step.get("params")
            if isinstance(params, dict):
                for key in ("TrueBranch", "FalseBranch", "loopBranch"):
                    branch = params.get(key)
                    if isinstance(branch, list):
                        ActionCrudService._annotate_steps_with_action_details(branch, detail_map)

    @staticmethod
    async def _get_tags_for_action(session, action_db_id: int) -> list[str]:
        """获取单个动作的标签列表"""
        result = await session.exec(
            select(TagModel.name)
            .join(CompositeActionTagLink, CompositeActionTagLink.tag_id == TagModel.id)
            .where(CompositeActionTagLink.composite_action_id == action_db_id)
        )
        return list(result.all())

    @staticmethod
    async def _get_tags_for_actions(session, action_db_ids: list[int]) -> dict[int, list[str]]:
        """批量获取多个动作的标签列表"""
        if not action_db_ids:
            return {}
        result = await session.exec(
            select(CompositeActionTagLink.composite_action_id, TagModel.name)
            .join(TagModel, TagModel.id == CompositeActionTagLink.tag_id)
            .where(CompositeActionTagLink.composite_action_id.in_(action_db_ids))
        )
        tags_map: dict[int, list[str]] = {aid: [] for aid in action_db_ids}
        for row in result.all():
            tags_map[row[0]].append(row[1])
        return tags_map

    @staticmethod
    async def _set_tags_for_action(session, action_db_id: int, tags: list[str]) -> None:
        """设置动作的标签（替换模式：先删旧关联，再建新关联）"""
        await session.exec(
            delete(CompositeActionTagLink)
            .where(CompositeActionTagLink.composite_action_id == action_db_id)
        )
        for tag_name in tags:
            tag_name = tag_name.strip()
            if not tag_name:
                continue
            result = await session.exec(
                select(TagModel).where(TagModel.name == tag_name)
            )
            tag = result.first()
            if not tag:
                tag = TagModel(name=tag_name)
                session.add(tag)
                await session.flush()
            link = CompositeActionTagLink(
                composite_action_id=action_db_id,
                tag_id=tag.id,
            )
            session.add(link)

    @staticmethod
    async def get_tags_for_action(action_db_id: int) -> list[str]:
        """获取动作的标签列表（独立会话）"""
        async with DatabaseSessionManager.async_session() as session:
            return await ActionCrudService._get_tags_for_action(session, action_db_id)

    @staticmethod
    async def get_tags_for_actions(action_db_ids: list[int]) -> dict[int, list[str]]:
        """批量获取动作的标签列表（独立会话）"""
        async with DatabaseSessionManager.async_session() as session:
            return await ActionCrudService._get_tags_for_actions(session, action_db_ids)

    @staticmethod
    async def validate_steps_referenced_actions(steps: List[Dict], mid: int | str) -> None:
        """校验 steps 中所有引用的 ca_ 操作是否可访问（不允许越权）
        
        在执行路径中调用，提前在校验阶段拦截越权引用。

        Raises:
            ActionNotFoundException: 引用的操作不存在
            ActionNotAccessibleException: 无权访问引用的操作
        """
        ca_ids = ActionCrudService._collect_ca_action_ids(steps)
        if ca_ids:
            await ActionCrudService.get_validated_action_details(ca_ids, mid)

    # ═══════════════ CRUD 方法 ═══════════════

    @staticmethod
    async def create(
        mid: int,
        action_id: str,
        name: str,
        action_type: BuiltinActionType = BuiltinActionType.COMPOSITE,
        parameters_schema: List[Dict] = None,
        steps: List[Dict] | None = None,
        is_composite: bool = False,
        description: str = "",
        tags: list[str] | None = None,
        input_vars: list[Dict] | None = None,
        output_vars: list[str] | None = None,
        is_public: bool = False,
        timeout: int = 30000,
        retry_on_error: bool = False,
        retry_times: int = 0,
        retry_delay: float = 1.0,
        log_enabled: bool = False,
        log_record_params: bool = True,
        log_record_result: bool = True,
        log_record_variables: bool = False,
        log_only_on_error: bool = False,
        log_max_payload_length: int = 4000,
        log_retention_days: int = 30,
    ) -> CompositeActionModel:
        # 为每个 step dict 添加 action_type（从 action_id 推断），确保 discriminated union 能匹配
        # 同时将 Pydantic 模型实例转为 dict，避免 JSON 序列化错误
        def _normalize_step(s: Dict | BaseWorkflowStep) -> dict:
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
                    (CompositeActionModel.mid == mid) & (
                        CompositeActionModel.name == name)
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
                input_vars=input_vars or [],
                output_vars=output_vars or [],
                is_public=is_public,
                log_enabled=log_enabled,
                log_record_params=log_record_params,
                log_record_result=log_record_result,
                log_record_variables=log_record_variables,
                log_only_on_error=log_only_on_error,
                log_max_payload_length=log_max_payload_length,
                log_retention_days=log_retention_days,
            )
            session.add(model)

            await session.commit()
            await session.refresh(model)
            if tags:
                await ActionCrudService._set_tags_for_action(session, model.id, tags)
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
                select(CompositeActionModel).where(
                    CompositeActionModel.action_id == action_id)
            )
            return result.first()

    @staticmethod
    async def get_name_map_by_action_ids(action_ids: List[str]) -> Dict[str, str]:
        """批量获取 action_id → name 的映射"""
        if not action_ids:
            return {}
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel.action_id, CompositeActionModel.name)
                .where(CompositeActionModel.action_id.in_(action_ids))
            )
            return {row[0]: row[1] for row in result.all()}

    @staticmethod
    def _apply_search_filters(query, name: str | None = None, tag: str | None = None, tag_exact: bool = True):
        """为查询添加 name 模糊搜索和 tag 筛选条件"""
        if name:
            query = query.where(CompositeActionModel.name.ilike(f"%{name}%"))
        if tag:
            tag_subquery = (
                select(CompositeActionTagLink.composite_action_id)
                .join(TagModel, TagModel.id == CompositeActionTagLink.tag_id)
            )
            if tag_exact:
                tag_subquery = tag_subquery.where(TagModel.name == tag)
            else:
                tag_subquery = tag_subquery.where(TagModel.name.ilike(f"%{tag}%"))
            query = query.where(CompositeActionModel.id.in_(tag_subquery))
        return query

    @staticmethod
    async def count_by_user(
        mid: int,
        filter_type: str = "all",
        name: str | None = None,
        tag: str | None = None,
        tag_exact: bool = True,
    ) -> int:
        from sqlmodel import func

        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(1))
            if filter_type == "private":  # private只检查是自己创建的就行了
                query = query.where(CompositeActionModel.mid == str(mid))
            elif filter_type == "public":
                query = query.where(CompositeActionModel.is_public == true())
            elif filter_type == "community":
                query = query.where((CompositeActionModel.mid != str(mid)) & (
                    CompositeActionModel.is_public == true()))
            elif filter_type == "verified":
                query = query.where(CompositeActionModel.is_verified == true())
            else:
                query = query.where((CompositeActionModel.mid == str(mid)) | (
                    CompositeActionModel.is_public == true()))

            query = ActionCrudService._apply_search_filters(query, name, tag, tag_exact)
            result = await session.exec(query)
            return result.one()

    @staticmethod
    async def list_by_user(
        mid: int,
        skip: int = 0,
        limit: int = 100,
        filter_type: str = "all",
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        name: str | None = None,
        tag: str | None = None,
        tag_exact: bool = True,
    ) -> List[CompositeActionModel]:
        from sqlmodel import col

        async with DatabaseSessionManager.async_session() as session:
            query = select(CompositeActionModel)
            if filter_type == "private":  # private只检查是自己创建的就行了
                query = query.where((CompositeActionModel.mid == str(mid)))
            elif filter_type == "public":
                query = query.where(CompositeActionModel.is_public == true())
            elif filter_type == "community":
                query = query.where((CompositeActionModel.mid != str(mid)) & (
                    CompositeActionModel.is_public == true()))
            elif filter_type == "verified":
                query = query.where(CompositeActionModel.is_verified == true())
            else:
                query = query.where((CompositeActionModel.mid == str(mid)) | (
                    CompositeActionModel.is_public == true()))

            query = ActionCrudService._apply_search_filters(query, name, tag, tag_exact)

            sort_field = getattr(CompositeActionModel,
                                 sort_by, CompositeActionModel.updated_at)
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
        parameters_schema: List[Dict] | None = None,
        steps: List[Dict] | None = None,
        tags: List[str] | None = None,
        input_vars: List[Dict] | None = None,
        output_vars: List[str] | None = None,
        is_composite: bool | None = None,
        timeout: int | None = None,
        retry_on_error: bool | None = None,
        retry_times: int | None = None,
        retry_delay: float | None = None,
        is_public: bool | None = None,
        log_enabled: bool | None = None,
        log_record_params: bool | None = None,
        log_record_result: bool | None = None,
        log_record_variables: bool | None = None,
        log_only_on_error: bool | None = None,
        log_max_payload_length: int | None = None,
        log_retention_days: int | None = None,
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
                await ActionCrudService._set_tags_for_action(session, model.id, tags)
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
            if log_enabled is not None:
                model.log_enabled = log_enabled
            if log_record_params is not None:
                model.log_record_params = log_record_params
            if log_record_result is not None:
                model.log_record_result = log_record_result
            if log_record_variables is not None:
                model.log_record_variables = log_record_variables
            if log_only_on_error is not None:
                model.log_only_on_error = log_only_on_error
            if log_max_payload_length is not None:
                model.log_max_payload_length = log_max_payload_length
            if log_retention_days is not None:
                model.log_retention_days = log_retention_days

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

            await session.exec(
                delete(CompositeActionTagLink)
                .where(CompositeActionTagLink.composite_action_id == model.id)
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
            stmt = update(CompositeActionModel).where(CompositeActionModel.id == id).values(
                likes_count=CompositeActionModel.likes_count + 1)
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            stmt = update(CompositeActionModel).where(CompositeActionModel.id == id).values(
                reports_count=CompositeActionModel.reports_count + 1)
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
                    (CompositeActionModel.mid == target_mid) & (
                        CompositeActionModel.name == new_name)
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
                parameters_schema=original.parameters_schema.copy(
                ) if original.parameters_schema else [],
                steps=original.steps.copy() if original.steps else [],
                input_vars=original.input_vars.copy() if original.input_vars else [],
                output_vars=original.output_vars.copy() if original.output_vars else [],
                is_public=False,
                forked_from_id=original.id,
                log_enabled=original.log_enabled,
                log_record_params=original.log_record_params,
                log_record_result=original.log_record_result,
                log_record_variables=original.log_record_variables,
                log_only_on_error=original.log_only_on_error,
                log_max_payload_length=original.log_max_payload_length,
                log_retention_days=original.log_retention_days,
            )

            session.add(new_model)
            await session.exec(
                update(CompositeActionModel).where(CompositeActionModel.id == original.id).values(
                    forks_count=CompositeActionModel.forks_count + 1)
            )

            await session.commit()
            await session.refresh(new_model)
            original_tags = await ActionCrudService._get_tags_for_action(session, original.id)
            if original_tags:
                await ActionCrudService._set_tags_for_action(session, new_model.id, original_tags)
                await session.commit()
                await session.refresh(new_model)
            return new_model

    @staticmethod
    async def list_tags_by_user(mid: int) -> List[str]:
        """获取用户所有自定义操作的去重标签列表"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(TagModel.name)
                .join(CompositeActionTagLink, CompositeActionTagLink.tag_id == TagModel.id)
                .join(CompositeActionModel, CompositeActionModel.id == CompositeActionTagLink.composite_action_id)
                .where(CompositeActionModel.mid == str(mid))
                .distinct()
            )
            return list(result.all())

    @staticmethod
    def _apply_filter_type(mid: int, filter_type: str):
        """根据 filter_type 构建可见性过滤条件，返回可直接用于 .where() 的条件"""
        if filter_type == "private":
            return CompositeActionModel.mid == str(mid)
        elif filter_type == "public":
            return CompositeActionModel.is_public == true()
        elif filter_type == "community":
            return (CompositeActionModel.mid != str(mid)) & (CompositeActionModel.is_public == true())
        elif filter_type == "verified":
            return CompositeActionModel.is_verified == true()
        else:
            return (CompositeActionModel.mid == str(mid)) | (CompositeActionModel.is_public == true())

    @staticmethod
    async def search_tags_by_user(
        mid: int,
        keyword: str | None = None,
        filter_type: str = "all",
        limit: int = 20,
    ) -> List[Dict]:
        """搜索标签并返回每个标签关联的操作数量

        Returns:
            [{"name": "tag1", "count": 3}, ...]
        """
        from sqlmodel import func

        async with DatabaseSessionManager.async_session() as session:
            query = (
                select(TagModel.name, func.count(CompositeActionModel.id).label("count"))
                .join(CompositeActionTagLink, CompositeActionTagLink.tag_id == TagModel.id)
                .join(CompositeActionModel, CompositeActionModel.id == CompositeActionTagLink.composite_action_id)
                .where(ActionCrudService._apply_filter_type(mid, filter_type))
                .group_by(TagModel.name)
            )
            if keyword:
                query = query.where(TagModel.name.ilike(f"%{keyword}%"))
            query = query.order_by(func.count(CompositeActionModel.id).desc()).limit(limit)
            result = await session.exec(query)
            return [{"name": row[0], "count": row[1]} for row in result.all()]

    @staticmethod
    async def search_names_by_user(
        mid: int,
        keyword: str | None = None,
        filter_type: str = "all",
        limit: int = 10,
    ) -> List[str]:
        """搜索操作名称（用于输入联想），返回去重的名称列表"""
        async with DatabaseSessionManager.async_session() as session:
            query = (
                select(CompositeActionModel.name)
                .where(ActionCrudService._apply_filter_type(mid, filter_type))
                .distinct()
            )
            if keyword:
                query = query.where(CompositeActionModel.name.ilike(f"%{keyword}%"))
            query = query.limit(limit)
            result = await session.exec(query)
            return list(result.all())


action_crud_svr = ActionCrudService()
