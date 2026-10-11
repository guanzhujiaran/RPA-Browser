"""
工作流 CRUD 服务
"""

from typing import Any, Dict, List
from datetime import datetime
from sqlmodel import select
from sqlalchemy import true, false, update

from app.models.database.workflow.models import (
    UserWorkflow,
    CompositeActionModel,
)
from app.models.common.exceptions.base_exception import NameAlreadyExistsException
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.snow_flake_gen import generate_workflow_id
from app.models.execution.action_params import normalize_composite_action_id


class WorkflowCrudService:
    """工作流 CRUD 服务"""

    @staticmethod
    async def create(
        mid: int,
        name: str,
        custom_action_id: int | None,
        description: str = "",
        browser_id: int | None = None,
        trigger_type: str = "manual",
        trigger_config: Dict | None = None,
        is_public: bool = False,
    ) -> UserWorkflow:
        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(UserWorkflow).where(
                    (UserWorkflow.mid == mid) & (UserWorkflow.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="工作流")

            new_workflow_id = await generate_workflow_id()
            model = UserWorkflow(
                workflow_id=new_workflow_id,
                name=name,
                custom_action_id=(
                    normalize_composite_action_id(custom_action_id)
                    if custom_action_id is not None
                    else None
                ),
                description=description,
                browser_id=browser_id,
                mid=mid,
                original_mid=mid,
                trigger_type=trigger_type,
                trigger_config=trigger_config or {},
                is_public=is_public,
            )
            session.add(model)

            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def get_by_id(id: int) -> UserWorkflow | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow).where(UserWorkflow.id == id)
            )
            return result.first()

    @staticmethod
    async def get_by_workflow_id(workflow_id: int) -> UserWorkflow | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow).where(
                    UserWorkflow.workflow_id == int(workflow_id)
                )
            )
            return result.first()

    @staticmethod
    async def count_by_user(mid: int, filter_type: str = "all") -> int:
        from sqlmodel import func

        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(UserWorkflow.id))
            if filter_type == "private":
                query = query.where(
                    (UserWorkflow.mid == str(mid)) & (UserWorkflow.is_public == false())
                )
            elif filter_type == "public":
                query = query.where(UserWorkflow.is_public == true())
            elif filter_type == "community":
                query = query.where(
                    (UserWorkflow.mid != str(mid)) & (UserWorkflow.is_public == true())
                )
            elif filter_type == "verified":
                query = query.where(UserWorkflow.is_verified == true())
            else:
                query = query.where(
                    (UserWorkflow.mid == str(mid)) | (UserWorkflow.is_public == true())
                )

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
    ) -> List[UserWorkflow]:
        from sqlmodel import col

        async with DatabaseSessionManager.async_session() as session:
            query = select(UserWorkflow)
            if filter_type == "private":
                query = query.where(
                    (UserWorkflow.mid == str(mid)) & (UserWorkflow.is_public == false())
                )
            elif filter_type == "public":
                query = query.where(UserWorkflow.is_public == true())
            elif filter_type == "community":
                query = query.where(
                    (UserWorkflow.mid != str(mid)) & (UserWorkflow.is_public == true())
                )
            elif filter_type == "verified":
                query = query.where(UserWorkflow.is_verified == true())
            else:
                query = query.where(
                    (UserWorkflow.mid == str(mid)) | (UserWorkflow.is_public == true())
                )

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
        custom_action_id: int | None = None,
        browser_id: int | None = None,
        trigger_type: str | None = None,
        trigger_config: Dict | None = None,
        is_enabled: bool | None = None,
        is_public: bool | None = None,
    ) -> UserWorkflow | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow).where(UserWorkflow.id == id)
            )
            model = result.first()
            if not model:
                return None

            if name is not None and name != model.name:
                existing = await session.exec(
                    select(UserWorkflow).where(
                        (UserWorkflow.mid == model.mid)
                        & (UserWorkflow.name == name)
                        & (UserWorkflow.id != id)
                    )
                )
                if existing.first():
                    raise NameAlreadyExistsException(name=name, name_type="工作流")
                model.name = name

            if description is not None:
                model.description = description
            if custom_action_id is not None:
                model.custom_action_id = normalize_composite_action_id(
                    custom_action_id
                )
            if browser_id is not None:
                model.browser_id = browser_id
            if trigger_type is not None:
                model.trigger_type = trigger_type
            if trigger_config is not None:
                model.trigger_config = trigger_config
            if is_enabled is not None:
                model.is_enabled = is_enabled
            if is_public is not None:
                model.is_public = is_public

            model.updated_at = datetime.now()
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def delete(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow).where(UserWorkflow.id == id)
            )
            model = result.first()
            if not model:
                return False

            if model.forked_from_id:
                await session.exec(
                    update(UserWorkflow)
                    .where(UserWorkflow.id == model.forked_from_id)
                    .values(forks_count=UserWorkflow.forks_count - 1)
                )

            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserWorkflow)
                .where(UserWorkflow.id == id)
                .values(is_enabled=True, updated_at=datetime.now())
            )
            await session.commit()
            return True

    @staticmethod
    async def disable(id: int) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserWorkflow)
                .where(UserWorkflow.id == id)
                .values(is_enabled=False, updated_at=datetime.now())
            )
            await session.commit()
            return True

    @staticmethod
    async def duplicate(id: int) -> UserWorkflow | None:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow).where(UserWorkflow.id == id)
            )
            original = result.first()
            if not original:
                return None

            new_workflow_id = await generate_workflow_id()
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
    async def fork(
        id: int, target_mid: int, new_name: str | None = None
    ) -> tuple[UserWorkflow | None, int]:
        """Fork 工作流，并递归深拷贝其引用的 Action 依赖树

        - 顶层 Action 如果属于他人，会通过 fork_deep 复制整棵第三方依赖树
        - 顶层 Action 如果已属于自己，直接保留引用
        - 浏览器是私有资源不继承

        Returns:
            (新工作流, 连带 Fork 的 Action 数量)；工作流不存在返回 (None, 0)
        """
        from app.services.execution.crud_service.action_crud import action_crud_svr

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow).where(UserWorkflow.id == id)
            )
            original = result.first()
            if not original:
                return None, 0

            if not original.is_public and str(original.mid) != str(target_mid):
                raise ValueError("只能 Fork 公开的工作流")

            # 提前取出需要的字段（session 关闭后属性会过期）
            original_custom_action_id = original.custom_action_id
            original_id = original.id
            original_name = original.name
            original_original_mid = original.original_mid
            original_trigger_type = original.trigger_type
            original_trigger_config = original.trigger_config
            original_is_public = original.is_public

        # ── 递归 Fork Action 依赖树 ──
        forked_action_count = 0
        new_custom_action_id = original_custom_action_id

        if original_custom_action_id:
            action_model = await action_crud_svr.get_by_action_id(
                original_custom_action_id
            )
            if not action_model:
                raise ValueError(
                    f"工作流关联的动作不存在: {original_custom_action_id}"
                )

            if str(action_model.mid) != str(target_mid):
                # 他人的 Action：递归深拷贝整棵依赖树
                top_action, all_actions = await action_crud_svr.fork_deep(
                    action_id=original_custom_action_id,
                    target_mid=target_mid,
                )
                new_custom_action_id = top_action.action_id
                forked_action_count = len(all_actions)
            # else: 自己的 Action 直接保留引用

        # ── 创建 Fork 的工作流外壳 ──
        new_workflow_id = await generate_workflow_id()
        if not new_name:
            new_name = f"{original_name} (Fork)"

        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(UserWorkflow).where(
                    (UserWorkflow.mid == target_mid)
                    & (UserWorkflow.name == new_name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=new_name, name_type="工作流")

            new_model = UserWorkflow(
                workflow_id=new_workflow_id,
                name=new_name,
                description=f"Forked from: {original_name}",
                mid=target_mid,
                original_mid=original_original_mid,
                custom_action_id=new_custom_action_id,
                # 浏览器是用户私有资源，Fork 后不继承（避免指向他人浏览器）；定时触发需重新选择
                browser_id=None,
                trigger_type=original_trigger_type,
                trigger_config=original_trigger_config,
                is_public=False,
                forked_from_id=original_id,
            )

            session.add(new_model)
            await session.exec(
                update(UserWorkflow)
                .where(UserWorkflow.id == original_id)
                .values(forks_count=UserWorkflow.forks_count + 1)
            )

            await session.commit()
            await session.refresh(new_model)
            return new_model, forked_action_count

    @staticmethod
    async def list_forks(
        workflow_id: int, skip: int = 0, limit: int = 50
    ) -> List[UserWorkflow]:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow)
                .where(UserWorkflow.forked_from_id == workflow_id)
                .order_by(UserWorkflow.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def sync_from_upstream(
        id: int, mid: int
    ) -> Dict[str, Any] | None:
        """将 Fork 的工作流同步到上游最新版本

        工作流本身只有触发配置等外壳字段，核心内容在 Action 中。
        此方法同步：
        1. 工作流的 trigger_config 等外壳配置（从上游工作流获取）
        2. 递归同步关联的 Action 依赖树

        Args:
            id: 用户 Fork 的工作流数据库 ID
            mid: 当前用户 mid

        Returns:
            同步结果 dict；工作流不存在返回 None
        """
        from app.services.execution.crud_service.action_crud import action_crud_svr

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow).where(UserWorkflow.id == id)
            )
            workflow = result.first()
            if not workflow:
                return None

            # 权限校验
            if str(workflow.mid) != str(mid):
                raise ValueError("无权操作此工作流")

            # 没有上游来源（非 Fork 的工作流）
            if not workflow.forked_from_id:
                return {
                    "workflow_id": workflow.workflow_id,
                    "updated": [],
                    "created": [],
                    "unavailable": [],
                    "workflow_config_updated": False,
                }

            # 查找上游工作流
            upstream_result = await session.exec(
                select(UserWorkflow).where(
                    UserWorkflow.id == workflow.forked_from_id
                )
            )
            upstream_wf = upstream_result.first()

            workflow_config_updated = False
            if upstream_wf:
                # 同步触发配置（不同步 browser_id，那是私有资源）
                if upstream_wf.trigger_config != workflow.trigger_config:
                    workflow.trigger_config = upstream_wf.trigger_config
                    workflow_config_updated = True
                if upstream_wf.trigger_type != workflow.trigger_type:
                    workflow.trigger_type = upstream_wf.trigger_type
                    workflow_config_updated = True
                workflow.updated_at = datetime.now()
                session.add(workflow)

            await session.commit()

            # 同步 Action 依赖树
            action_result = {
                "updated": [],
                "created": [],
                "unavailable": [],
            }
            if workflow.custom_action_id:
                action_result = await action_crud_svr.sync_from_upstream(
                    action_id=workflow.custom_action_id,
                    mid=mid,
                )

            return {
                "workflow_id": workflow.workflow_id,
                "updated": action_result.get("updated", []),
                "created": action_result.get("created", []),
                "unavailable": action_result.get("unavailable", []),
                "workflow_config_updated": workflow_config_updated,
            }

    @staticmethod
    async def list_scheduling() -> List[UserWorkflow]:
        """列出所有需要注册定时任务的工作流（启用 + cron 触发 + 已指定浏览器）"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow).where(
                    (UserWorkflow.is_enabled == true())
                    & (UserWorkflow.trigger_type == "cron")
                    & (UserWorkflow.browser_id.is_not(None))
                )
            )
            return result.all()

    @staticmethod
    async def update_run_state(
        id: int,
        status: str | None = None,
        last_run_at: datetime | None = None,
        next_run_at: datetime | None = None,
    ) -> bool:
        """更新工作流的运行状态（定时调度与手动运行共用）"""
        values: Dict[str, Any] = {}
        if status is not None:
            values["last_run_status"] = status
        if last_run_at is not None:
            values["last_run_at"] = last_run_at
        if next_run_at is not None:
            values["next_run_at"] = next_run_at
        if not values:
            return False

        async with DatabaseSessionManager.async_session() as session:
            await session.exec(
                update(UserWorkflow).where(UserWorkflow.id == id).values(**values)
            )
            await session.commit()
            return True


workflow_crud_svr = WorkflowCrudService()
