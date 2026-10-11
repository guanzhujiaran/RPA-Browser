"""
操作 CRUD 服务
"""

from sqlalchemy import or_, and_, true, update
from typing import Any, Dict, List
from datetime import datetime
from sqlmodel import select, delete

from app.models.database.workflow.models import (
    CompositeActionModel,
    BuiltinActionType,
    TagModel,
    CompositeActionTagLink,
)
from app.models.execution.action_params import (
    BaseWorkflowStep,
    is_composite_action_id,
    normalize_composite_action_id,
)
from app.models.common.exceptions.base_exception import NameAlreadyExistsException
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.snow_flake_gen import generate_action_id


class ActionCrudService:
    """操作 CRUD 服务"""

    # ═══════════════ steps 中引用的自定义操作校验与详情注解 ═══════════════

    # steps JSON 中所有可能嵌套子步骤列表的字段路径
    _NESTED_BRANCH_KEYS = (
        "TrueBranch",
        "FalseBranch",
        "loopBranch",
        "steps",  # CompositeParams.steps
    )
    _STEP_LEVEL_NESTED_KEYS = ("on_error_branch", "children")

    @staticmethod
    def _collect_ca_action_ids(steps: list[Dict]) -> list[int]:
        """递归收集 steps 中所有复合动作引用 ID（雪花 int，含所有嵌套分支路径）"""
        ids: list[int] = []
        for step in steps:
            action_id = step.get("action_id")
            if is_composite_action_id(action_id):
                ids.append(normalize_composite_action_id(action_id))
            # step 层级的嵌套步骤
            for key in ActionCrudService._STEP_LEVEL_NESTED_KEYS:
                branch = step.get(key)
                if isinstance(branch, list):
                    ids.extend(ActionCrudService._collect_ca_action_ids(branch))
            # params 中的嵌套分支
            params = step.get("params")
            if isinstance(params, dict):
                for key in ActionCrudService._NESTED_BRANCH_KEYS:
                    branch = params.get(key)
                    if isinstance(branch, list):
                        ids.extend(ActionCrudService._collect_ca_action_ids(branch))
        return ids

    @staticmethod
    def _replace_ca_action_ids_in_steps(
        steps: list[Dict], id_map: Dict[int, int]
    ) -> list[Dict]:
        """递归替换 steps 中所有复合动作 ID，返回新的 steps（深拷贝 + 替换）

        Args:
            steps: 原始步骤列表（dict 形式，从 JSON 列读出）
            id_map: {旧复合动作 ID: 新复合动作 ID} 映射（均为雪花 int）
        """
        new_steps: list[Dict] = []
        for step in steps:
            new_step = dict(step)  # shallow copy per step
            action_id = new_step.get("action_id")
            if is_composite_action_id(action_id):
                old_id = normalize_composite_action_id(action_id)
                if old_id in id_map:
                    new_step["action_id"] = id_map[old_id]

            # step 层级嵌套
            for key in ActionCrudService._STEP_LEVEL_NESTED_KEYS:
                branch = new_step.get(key)
                if isinstance(branch, list):
                    new_step[key] = ActionCrudService._replace_ca_action_ids_in_steps(
                        branch, id_map
                    )

            # params 中的嵌套分支
            params = new_step.get("params")
            if isinstance(params, dict):
                new_params = dict(params)
                changed = False
                for key in ActionCrudService._NESTED_BRANCH_KEYS:
                    branch = new_params.get(key)
                    if isinstance(branch, list):
                        new_params[key] = (
                            ActionCrudService._replace_ca_action_ids_in_steps(
                                branch, id_map
                            )
                        )
                        changed = True
                if changed:
                    new_step["params"] = new_params

            new_steps.append(new_step)
        return new_steps

    @staticmethod
    async def get_validated_action_details(
        action_ids: List[int],
        mid: int | str,
    ) -> Dict[int, Dict]:
        """批量获取自定义操作的完整信息，并校验权限（仅返回自己或公开的）

        Args:
            action_ids: 复合动作雪花 ID 列表（会自动去重）
            mid: 当前用户 mid

        Returns:
            action_id（int）→ 可序列化的详情字典（不含 steps，避免无限递归）

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

        deduped = list({normalize_composite_action_id(a) for a in action_ids})
        mid_str = str(mid)

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel).where(
                    CompositeActionModel.action_id.in_(deduped)
                )
            )
            models = result.all()
            model_map: Dict[int, CompositeActionModel] = {
                m.action_id: m for m in models
            }
            tags_map = await ActionCrudService._get_tags_for_actions(
                session, [m.id for m in models]
            )

        detail_map: Dict[int, Dict] = {}
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
                "action_type": str(model.action_type.value)
                if hasattr(model.action_type, "value")
                else str(model.action_type),
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
        detail_map: Dict[int, Dict],
    ) -> None:
        """递归为 steps 中的复合动作引用添加 action_detail 字段"""
        for step in steps:
            action_id = step.get("action_id")
            if is_composite_action_id(action_id):
                step["action_detail"] = detail_map.get(
                    normalize_composite_action_id(action_id)
                )
            params = step.get("params")
            if isinstance(params, dict):
                for key in ("TrueBranch", "FalseBranch", "loopBranch"):
                    branch = params.get(key)
                    if isinstance(branch, list):
                        ActionCrudService._annotate_steps_with_action_details(
                            branch, detail_map
                        )

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
    async def _get_tags_for_actions(
        session, action_db_ids: list[int]
    ) -> dict[int, list[str]]:
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
            delete(CompositeActionTagLink).where(
                CompositeActionTagLink.composite_action_id == action_db_id
            )
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
    async def validate_steps_referenced_actions(
        steps: List[Dict], mid: int | str
    ) -> None:
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
        icon_series: int = 0,
        icon_id: int = 0,
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
                if hasattr(s, "model_dump"):
                    s = s.model_dump(exclude_none=True, mode="json")
                else:
                    s = dict(s) if s else {}
            if "action_type" not in s and "action_id" in s:
                if is_composite_action_id(s["action_id"]):
                    s["action_type"] = BuiltinActionType.COMPOSITE.value
                else:
                    s["action_type"] = s["action_id"]
            if "children" in s and s["children"]:
                s["children"] = [_normalize_step(c) for c in s["children"]]
            return s

        normalized_steps = [_normalize_step(s) for s in (steps or [])]

        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(CompositeActionModel).where(
                    (CompositeActionModel.mid == mid)
                    & (CompositeActionModel.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="操作")

            new_action_id = await generate_action_id()
            model = CompositeActionModel(
                action_id=new_action_id,
                name=name,
                action_type=action_type,
                description=description,
                icon_series=icon_series,
                icon_id=icon_id,
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
            result = await session.exec(
                select(CompositeActionModel).where(CompositeActionModel.id == id)
            )
            return result.first()

    @staticmethod
    async def get_by_action_id(action_id: int) -> CompositeActionModel | None:
        """通过雪花 action_id 查找操作"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel).where(
                    CompositeActionModel.action_id
                    == normalize_composite_action_id(action_id)
                )
            )
            return result.first()

    @staticmethod
    async def get_name_map_by_action_ids(action_ids: List[int]) -> Dict[int, str]:
        """批量获取 action_id（雪花 int）→ name 的映射"""
        if not action_ids:
            return {}
        norm_ids = [normalize_composite_action_id(a) for a in action_ids]
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel.action_id, CompositeActionModel.name).where(
                    CompositeActionModel.action_id.in_(norm_ids)
                )
            )
            return {row[0]: row[1] for row in result.all()}

    @staticmethod
    def _apply_search_filters(
        query, name: str | None = None, tag: str | None = None, tag_exact: bool = True
    ):
        """为查询添加 name 模糊搜索和 tag 筛选条件"""
        if name:
            query = query.where(CompositeActionModel.name.ilike(f"%{name}%"))
        if tag:
            tag_subquery = select(CompositeActionTagLink.composite_action_id).join(
                TagModel, TagModel.id == CompositeActionTagLink.tag_id
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
                query = query.where(
                    (CompositeActionModel.mid != str(mid))
                    & (CompositeActionModel.is_public == true())
                )
            elif filter_type == "verified":
                query = query.where(CompositeActionModel.is_verified == true())
            else:
                query = query.where(
                    (CompositeActionModel.mid == str(mid))
                    | (CompositeActionModel.is_public == true())
                )

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
                query = query.where(
                    (CompositeActionModel.mid != str(mid))
                    & (CompositeActionModel.is_public == true())
                )
            elif filter_type == "verified":
                query = query.where(CompositeActionModel.is_verified == true())
            else:
                query = query.where(
                    (CompositeActionModel.mid == str(mid))
                    | (CompositeActionModel.is_public == true())
                )

            query = ActionCrudService._apply_search_filters(query, name, tag, tag_exact)

            sort_field = getattr(
                CompositeActionModel, sort_by, CompositeActionModel.updated_at
            )
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
        icon_series: int | None = None,
        icon_id: int | None = None,
        log_enabled: bool | None = None,
        log_record_params: bool | None = None,
        log_record_result: bool | None = None,
        log_record_variables: bool | None = None,
        log_only_on_error: bool | None = None,
        log_max_payload_length: int | None = None,
        log_retention_days: int | None = None,
    ) -> CompositeActionModel | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel).where(CompositeActionModel.id == id)
            )
            model = result.first()
            if not model:
                return None

            if name is not None and name != model.name:
                existing = await session.exec(
                    select(CompositeActionModel).where(
                        (CompositeActionModel.mid == model.mid)
                        & (CompositeActionModel.name == name)
                        & (CompositeActionModel.id != id)
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
            if icon_series is not None:
                model.icon_series = icon_series
            if icon_id is not None:
                model.icon_id = icon_id
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
            result = await session.exec(
                select(CompositeActionModel).where(CompositeActionModel.id == id)
            )
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
                delete(CompositeActionTagLink).where(
                    CompositeActionTagLink.composite_action_id == model.id
                )
            )

            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel).where(CompositeActionModel.id == id)
            )
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
            result = await session.exec(
                select(CompositeActionModel).where(CompositeActionModel.id == id)
            )
            model = result.first()
            if not model:
                return False
            model.is_enabled = False
            model.updated_at = datetime.now()
            await session.commit()
            return True

    @staticmethod
    async def list_forks(
        action_id: int, skip: int = 0, limit: int = 50
    ) -> List[CompositeActionModel]:
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
    async def fork(
        id: int, target_mid: int, new_name: str | None = None
    ) -> CompositeActionModel | None:
        """Fork Action（递归深拷贝所有第三方依赖）

        兼容旧接口：按数据库 id 查询后委托给 fork_deep。
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CompositeActionModel).where(CompositeActionModel.id == id)
            )
            original = result.first()
            if not original:
                return None

            action_id = original.action_id

        top_model, _ = await ActionCrudService.fork_deep(
            action_id=action_id,
            target_mid=target_mid,
            new_name=new_name,
        )
        return top_model

    # ═══════════════ 递归深拷贝 Fork（安全快照） ═══════════════

    @staticmethod
    async def fork_deep(
        action_id: int,
        target_mid: int,
        new_name: str | None = None,
    ) -> tuple[CompositeActionModel, list[CompositeActionModel]]:
        """递归深拷贝一个 Action 及其所有第三方复合动作依赖

        遍历被 Fork Action 的 steps 依赖树，把所有属于其他用户的复合动作引用
        都复制一份给 target_mid，并把 steps 中的旧雪花 ID 替换为新 ID。
        属于 target_mid 自己的引用保持不变（无需复制自己的资源）。

        整棵树在同一个数据库事务中完成，任何失败则全部回滚。

        Args:
            action_id: 要 Fork 的顶层 Action 的雪花 action_id
            target_mid: Fork 目标用户 mid
            new_name: 顶层 Action 的新名称（可选）

        Returns:
            (顶层新 Action, 所有新建的 Action 列表[含顶层])
        """
        import copy

        target_mid_str = str(target_mid)
        # 旧 action_id → 新 action_id 的映射（含循环引用去重）
        id_map: Dict[int, int] = {}
        # 所有新建的 model（含顶层），供调用方使用
        all_new_models: list[CompositeActionModel] = []

        async with DatabaseSessionManager.async_session() as session:

            async def _fork_one(
                src_action_id: int, *, is_top_level: bool
            ) -> int:
                """递归 Fork 单个 Action，返回新的雪花 action_id。

                - 如果该 Action 已在 id_map 中（循环引用/共享依赖），直接返回已有映射
                - 如果该 Action 属于 target_mid 自己，保留原 ID 不复制
                """
                src_action_id = normalize_composite_action_id(src_action_id)
                # 已经 Fork 过了（循环引用或多个步骤引用同一子 Action）
                if src_action_id in id_map:
                    return id_map[src_action_id]

                result = await session.exec(
                    select(CompositeActionModel).where(
                        CompositeActionModel.action_id == src_action_id
                    )
                )
                original = result.first()
                if not original:
                    from app.models.common.exceptions.base_exception import (
                        ActionNotFoundException,
                    )

                    raise ActionNotFoundException(src_action_id)

                # 自己的 Action 不需要 Fork，直接保留原引用
                if str(original.mid) == target_mid_str:
                    id_map[src_action_id] = src_action_id
                    return src_action_id

                # 第三方 Action 必须是公开的
                if not original.is_public:
                    from app.models.common.exceptions.base_exception import (
                        ActionNotAccessibleException,
                    )

                    raise ActionNotAccessibleException(src_action_id)

                # 先占位，防止循环引用导致无限递归
                new_aid = await generate_action_id()
                id_map[src_action_id] = new_aid

                # 深拷贝 steps（先完整深拷贝，稍后统一替换 ID）
                cloned_steps = copy.deepcopy(original.steps) if original.steps else []

                # 递归处理 steps 中引用的所有子 Action
                child_ids = ActionCrudService._collect_ca_action_ids(cloned_steps)
                # 去重但保持顺序（便于阅读），不含自己
                seen: set[int] = set()
                for cid in child_ids:
                    if cid not in seen:
                        seen.add(cid)
                        await _fork_one(cid, is_top_level=False)

                # 所有子 Action 处理完毕后，统一替换 steps 中的 ID
                replaced_steps = ActionCrudService._replace_ca_action_ids_in_steps(
                    cloned_steps, id_map
                )

                # 命名：顶层使用用户指定名称；子依赖自动加后缀
                if is_top_level and new_name:
                    action_name = new_name
                else:
                    action_name = f"{original.name} (Fork)"

                # 处理重名（同名自动加序号）
                final_name = action_name
                name_idx = 2
                while True:
                    existing = await session.exec(
                        select(CompositeActionModel).where(
                            (CompositeActionModel.mid == target_mid_str)
                            & (CompositeActionModel.name == final_name)
                        )
                    )
                    if not existing.first():
                        break
                    if is_top_level and new_name:
                        # 顶层用户指定名称冲突时抛异常（保持与旧 fork 一致的体验）
                        raise NameAlreadyExistsException(
                            name=final_name, name_type="操作"
                        )
                    final_name = f"{action_name} ({name_idx})"
                    name_idx += 1

                new_model = CompositeActionModel(
                    action_id=new_aid,
                    name=final_name,
                    action_type=original.action_type,
                    description=f"Forked from: {original.name}",
                    mid=target_mid,
                    original_mid=original.original_mid,
                    icon_series=original.icon_series,
                    icon_id=original.icon_id,
                    timeout=original.timeout,
                    is_composite=original.is_composite,
                    parameters_schema=copy.deepcopy(original.parameters_schema)
                    if original.parameters_schema
                    else [],
                    steps=replaced_steps,
                    input_vars=copy.deepcopy(original.input_vars)
                    if original.input_vars
                    else [],
                    output_vars=copy.deepcopy(original.output_vars)
                    if original.output_vars
                    else [],
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
                await session.flush()  # 拿到 new_model.id
                all_new_models.append(new_model)

                # 更新原 Action forks_count
                await session.exec(
                    update(CompositeActionModel)
                    .where(CompositeActionModel.id == original.id)
                    .values(forks_count=CompositeActionModel.forks_count + 1)
                )

                # 复制标签
                original_tags = await ActionCrudService._get_tags_for_action(
                    session, original.id
                )
                if original_tags:
                    await ActionCrudService._set_tags_for_action(
                        session, new_model.id, original_tags
                    )

                return new_aid

            # ── 开始递归 Fork ──
            top_new_id = await _fork_one(action_id, is_top_level=True)
            await session.commit()

            # refresh 所有新建 model 以获取 DB 默认值
            top_model = None
            for m in all_new_models:
                await session.refresh(m)
                if m.action_id == top_new_id:
                    top_model = m

            return top_model, list(all_new_models)

    # ═══════════════ 从上游同步最新内容 ═══════════════

    @staticmethod
    async def sync_from_upstream(
        action_id: int,
        mid: int,
    ) -> Dict[str, Any]:
        """将 Fork 的 Action 同步到原作者最新版本（含递归依赖树）

        通过 forked_from_id 找到上游 Action，拉取最新 steps 和配置，
        递归处理依赖树中所有第三方复合动作引用：
        - 用户已有对应 Fork 副本（通过 forked_from_id 匹配）→ 更新内容
        - 上游新增的依赖 → 自动新建 Fork
        - 上游已删除/私有化的依赖 → 报告但不阻塞整体同步
        - 用户自己的 Action → 保持不变

        Args:
            action_id: 用户 Fork 副本的雪花 action_id
            mid: 当前用户 mid（用于权限校验）

        Returns:
            {
                "action_id": 顶层 action_id,
                "updated": 被更新内容的 Action 名称列表,
                "created": 新 Fork 的 Action 名称列表,
                "unavailable": 上游已不可用的 action_id 列表,
            }
        """
        import copy

        mid_str = str(mid)
        updated_names: list[str] = []
        created_names: list[str] = []
        unavailable_ids: list[int] = []

        async with DatabaseSessionManager.async_session() as session:

            async def _find_forked_copy(
                upstream_db_id: int,
            ) -> CompositeActionModel | None:
                """通过 forked_from_id 查找用户已有的 Fork 副本"""
                result = await session.exec(
                    select(CompositeActionModel).where(
                        (CompositeActionModel.forked_from_id == upstream_db_id)
                        & (CompositeActionModel.mid == mid_str)
                    )
                )
                return result.first()

            async def _sync_one(
                fork_aid: int, *, is_top_level: bool
            ) -> int:
                """同步单个 Fork 副本，返回当前应使用的 action_id（通常是自身）。

                递归处理其 steps 中的子依赖。
                """
                fork_aid = normalize_composite_action_id(fork_aid)
                # 获取当前 Fork 副本
                result = await session.exec(
                    select(CompositeActionModel).where(
                        CompositeActionModel.action_id == fork_aid
                    )
                )
                fork_model = result.first()
                if not fork_model:
                    from app.models.common.exceptions.base_exception import (
                        ActionNotFoundException,
                    )

                    raise ActionNotFoundException(fork_aid)

                # 自己原创的 Action（没有 forked_from_id）无需同步
                if not fork_model.forked_from_id:
                    return fork_aid

                # 查找上游
                upstream_result = await session.exec(
                    select(CompositeActionModel).where(
                        CompositeActionModel.id == fork_model.forked_from_id
                    )
                )
                upstream = upstream_result.first()

                if not upstream or not upstream.is_public:
                    # 上游已删除或私有化：保留当前副本不动，记录不可用
                    if upstream:
                        unavailable_ids.append(upstream.action_id)
                    # 仍然递归处理当前 steps 里的子依赖（它们可能有独立的上游）
                    child_ids = ActionCrudService._collect_ca_action_ids(
                        fork_model.steps or []
                    )
                    seen: set[int] = set()
                    for cid in child_ids:
                        if cid not in seen:
                            seen.add(cid)
                            await _sync_child(cid)
                    return fork_aid

                # 上游可用：获取最新 steps 深拷贝
                upstream_steps = (
                    copy.deepcopy(upstream.steps) if upstream.steps else []
                )

                # 递归同步子依赖，构建 上游action_id → 用户action_id 映射
                # 上游 steps 中的雪花 ID 是上游视角的，需要映射回用户视角
                upstream_to_user_map: Dict[int, int] = {}

                child_ids = ActionCrudService._collect_ca_action_ids(upstream_steps)
                seen: set[int] = set()
                for cid in child_ids:
                    if cid not in seen:
                        seen.add(cid)
                        user_aid = await _sync_child(cid)
                        upstream_to_user_map[cid] = user_aid

                # 替换 steps 中的 ID
                replaced_steps = ActionCrudService._replace_ca_action_ids_in_steps(
                    upstream_steps, upstream_to_user_map
                )

                # 更新当前 Fork 副本的内容（保留名称、mid、action_id、forked_from_id、is_public）
                fork_model.steps = replaced_steps
                fork_model.action_type = upstream.action_type
                fork_model.is_composite = upstream.is_composite
                fork_model.parameters_schema = (
                    copy.deepcopy(upstream.parameters_schema)
                    if upstream.parameters_schema
                    else []
                )
                fork_model.input_vars = (
                    copy.deepcopy(upstream.input_vars)
                    if upstream.input_vars
                    else []
                )
                fork_model.output_vars = (
                    copy.deepcopy(upstream.output_vars)
                    if upstream.output_vars
                    else []
                )
                fork_model.icon_series = upstream.icon_series
                fork_model.icon_id = upstream.icon_id
                fork_model.timeout = upstream.timeout
                fork_model.retry_on_error = upstream.retry_on_error
                fork_model.retry_times = upstream.retry_times
                fork_model.retry_delay = upstream.retry_delay
                fork_model.log_enabled = upstream.log_enabled
                fork_model.log_record_params = upstream.log_record_params
                fork_model.log_record_result = upstream.log_record_result
                fork_model.log_record_variables = upstream.log_record_variables
                fork_model.log_only_on_error = upstream.log_only_on_error
                fork_model.log_max_payload_length = upstream.log_max_payload_length
                fork_model.log_retention_days = upstream.log_retention_days
                fork_model.description = f"Forked from: {upstream.name}"
                fork_model.updated_at = datetime.now()

                session.add(fork_model)
                updated_names.append(fork_model.name)

                # 同步标签
                upstream_tags = await ActionCrudService._get_tags_for_action(
                    session, upstream.id
                )
                await ActionCrudService._set_tags_for_action(
                    session, fork_model.id, upstream_tags
                )

                return fork_aid

            async def _sync_child(upstream_aid: int) -> int:
                """处理上游 steps 中引用的一个子 Action，返回用户视角的雪花 action_id。

                - 如果上游子 Action 属于当前用户自己 → 直接返回原 ID
                - 如果用户已有 Fork 副本 → 递归同步该副本
                - 如果用户没有副本 → 新建 Fork
                - 如果上游子 Action 不可用 → 尝试在用户已有副本中找，找不到则报错
                """
                upstream_aid = normalize_composite_action_id(upstream_aid)
                # 查找上游子 Action
                result = await session.exec(
                    select(CompositeActionModel).where(
                        CompositeActionModel.action_id == upstream_aid
                    )
                )
                upstream_child = result.first()

                if not upstream_child:
                    # 上游引用了不存在的 Action（数据异常），无法处理
                    unavailable_ids.append(upstream_aid)
                    return upstream_aid  # 保留旧引用，执行时会报错

                # 如果这个 Action 本来就是用户自己的
                if str(upstream_child.mid) == mid_str:
                    return upstream_aid

                # 必须公开
                if not upstream_child.is_public:
                    # 尝试找用户已有的旧 Fork 副本
                    existing_fork = await _find_forked_copy(upstream_child.id)
                    if existing_fork:
                        unavailable_ids.append(upstream_aid)
                        return existing_fork.action_id
                    unavailable_ids.append(upstream_aid)
                    return upstream_aid

                # 查找用户是否已有该上游的 Fork 副本
                existing_fork = await _find_forked_copy(upstream_child.id)
                if existing_fork:
                    # 已有副本：递归同步
                    return await _sync_one(
                        existing_fork.action_id, is_top_level=False
                    )

                # 没有副本：新建 Fork（类似 fork_deep 的逻辑）
                new_aid = await generate_action_id()

                child_steps = (
                    copy.deepcopy(upstream_child.steps)
                    if upstream_child.steps
                    else []
                )
                # 递归处理孙子依赖
                sub_map: Dict[int, int] = {}
                sub_ids = ActionCrudService._collect_ca_action_ids(child_steps)
                seen: set[int] = set()
                for sid in sub_ids:
                    if sid not in seen:
                        seen.add(sid)
                        sub_map[sid] = await _sync_child(sid)

                replaced = ActionCrudService._replace_ca_action_ids_in_steps(
                    child_steps, sub_map
                )

                # 自动命名避免冲突
                base_name = f"{upstream_child.name} (Fork)"
                final_name = base_name
                name_idx = 2
                while True:
                    existing_name = await session.exec(
                        select(CompositeActionModel).where(
                            (CompositeActionModel.mid == mid_str)
                            & (CompositeActionModel.name == final_name)
                        )
                    )
                    if not existing_name.first():
                        break
                    final_name = f"{base_name} ({name_idx})"
                    name_idx += 1

                new_model = CompositeActionModel(
                    action_id=new_aid,
                    name=final_name,
                    action_type=upstream_child.action_type,
                    description=f"Forked from: {upstream_child.name}",
                    mid=mid,
                    original_mid=upstream_child.original_mid,
                    icon_series=upstream_child.icon_series,
                    icon_id=upstream_child.icon_id,
                    timeout=upstream_child.timeout,
                    is_composite=upstream_child.is_composite,
                    parameters_schema=copy.deepcopy(upstream_child.parameters_schema)
                    if upstream_child.parameters_schema
                    else [],
                    steps=replaced,
                    input_vars=copy.deepcopy(upstream_child.input_vars)
                    if upstream_child.input_vars
                    else [],
                    output_vars=copy.deepcopy(upstream_child.output_vars)
                    if upstream_child.output_vars
                    else [],
                    is_public=False,
                    forked_from_id=upstream_child.id,
                    log_enabled=upstream_child.log_enabled,
                    log_record_params=upstream_child.log_record_params,
                    log_record_result=upstream_child.log_record_result,
                    log_record_variables=upstream_child.log_record_variables,
                    log_only_on_error=upstream_child.log_only_on_error,
                    log_max_payload_length=upstream_child.log_max_payload_length,
                    log_retention_days=upstream_child.log_retention_days,
                )
                session.add(new_model)
                await session.flush()
                created_names.append(final_name)

                # 更新 forks_count
                await session.exec(
                    update(CompositeActionModel)
                    .where(CompositeActionModel.id == upstream_child.id)
                    .values(forks_count=CompositeActionModel.forks_count + 1)
                )

                # 复制标签
                upstream_tags = await ActionCrudService._get_tags_for_action(
                    session, upstream_child.id
                )
                if upstream_tags:
                    await ActionCrudService._set_tags_for_action(
                        session, new_model.id, upstream_tags
                    )

                return new_aid

            # ── 开始同步顶层 ──
            await _sync_one(action_id, is_top_level=True)
            await session.commit()

        return {
            "action_id": action_id,
            "updated": updated_names,
            "created": created_names,
            "unavailable": unavailable_ids,
        }

    @staticmethod
    async def list_tags_by_user(mid: int) -> List[str]:
        """获取用户所有自定义操作的去重标签列表"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(TagModel.name)
                .join(
                    CompositeActionTagLink, CompositeActionTagLink.tag_id == TagModel.id
                )
                .join(
                    CompositeActionModel,
                    CompositeActionModel.id
                    == CompositeActionTagLink.composite_action_id,
                )
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
            return (CompositeActionModel.mid != str(mid)) & (
                CompositeActionModel.is_public == true()
            )
        elif filter_type == "verified":
            return CompositeActionModel.is_verified == true()
        else:
            return (CompositeActionModel.mid == str(mid)) | (
                CompositeActionModel.is_public == true()
            )

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
                select(
                    TagModel.name, func.count(CompositeActionModel.id).label("count")
                )
                .join(
                    CompositeActionTagLink, CompositeActionTagLink.tag_id == TagModel.id
                )
                .join(
                    CompositeActionModel,
                    CompositeActionModel.id
                    == CompositeActionTagLink.composite_action_id,
                )
                .where(ActionCrudService._apply_filter_type(mid, filter_type))
                .group_by(TagModel.name)
            )
            if keyword:
                query = query.where(TagModel.name.ilike(f"%{keyword}%"))
            query = query.order_by(func.count(CompositeActionModel.id).desc()).limit(
                limit
            )
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
