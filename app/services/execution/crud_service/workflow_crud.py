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
from app.models.workflow.models import UpstreamSyncFlag
from app.models.workflow.notify_flags import NotifyChannelFlags
from app.models.common.exceptions.base_exception import NameAlreadyExistsException
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.snow_flake_gen import generate_workflow_id
from app.models.execution.action_params import normalize_composite_action_id


def merge_variables_patch(
    current: Dict | None, patch: Dict | None
) -> Dict:
    """把增量补丁合并到现有变量上（纯函数，见 docs/工作流变量增量更新计划书.md）

    - 只覆盖 patch 里给出的键；
    - 值为 ``None`` 表示删除该键；
    - patch 为空时返回 current 的浅拷贝（不共享引用）。
    """
    merged: Dict = dict(current or {})
    for key, value in (patch or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


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
        notify_inbox_on_start: bool = False,
        notify_inbox_on_end: bool = True,
        notify_push_on_start: bool = False,
        notify_push_on_end: bool = True,
        log_enabled: bool = False,
        log_debug: bool | None = None,
        resume_on_restart: bool = False,
        auto_sync_upstream: bool = False,
        browser_ids: list[int] | None = None,
        concurrent_exec: bool = False,
        variables: Dict | None = None,
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
            notify_flags = NotifyChannelFlags(
                inbox_on_start=notify_inbox_on_start,
                inbox_on_end=notify_inbox_on_end,
                push_on_start=notify_push_on_start,
                push_on_end=notify_push_on_end,
            )
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
                notify_inbox_on_start=notify_flags.inbox_on_start,
                notify_inbox_on_end=notify_flags.inbox_on_end,
                notify_push_on_start=notify_flags.push_on_start,
                notify_push_on_end=notify_flags.push_on_end,
                # 旧字段（通道混合）保持为派生值，旧读方（管理端 / 导出）不会看到「全关」
                notify_on_start=notify_flags.legacy_on_start,
                notify_on_end=notify_flags.legacy_on_end,
                log_enabled=log_enabled,
                log_debug=log_debug,
                resume_on_restart=resume_on_restart,
                auto_sync_upstream=auto_sync_upstream,
                browser_ids=browser_ids,
                concurrent_exec=concurrent_exec,
                variables=variables or {},
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
    def _apply_scope_filter(query, mid: int | None, filter_type: str):
        """按「我的 / 公开 / 社区」范围过滤工作流查询。

        - mid 为 None（未登录的游客）：只允许看到公开内容，private 恒为空；
          community / public / all 一律收敛为 is_public，verified 额外要求已认证；
        - mid 有值：按原有分支过滤（community 为自己之外的公开内容）。
        """
        if mid is None:
            if filter_type == "private":
                return query.where(false())
            query = query.where(UserWorkflow.is_public == true())
            if filter_type == "verified":
                query = query.where(UserWorkflow.is_verified == true())
            return query

        if filter_type == "private":
            return query.where(
                (UserWorkflow.mid == str(mid)) & (UserWorkflow.is_public == false())
            )
        if filter_type == "public":
            return query.where(UserWorkflow.is_public == true())
        if filter_type == "community":
            return query.where(
                (UserWorkflow.mid != str(mid)) & (UserWorkflow.is_public == true())
            )
        if filter_type == "verified":
            return query.where(UserWorkflow.is_verified == true())
        return query.where(
            (UserWorkflow.mid == str(mid)) | (UserWorkflow.is_public == true())
        )

    @staticmethod
    async def count_by_user(
        mid: int | None, filter_type: str = "all", browser_id: int | None = None
    ) -> int:
        from sqlmodel import func

        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(UserWorkflow.id))
            query = WorkflowCrudService._apply_scope_filter(query, mid, filter_type)
            if browser_id is not None:
                query = query.where(UserWorkflow.browser_id == browser_id)

            result = await session.exec(query)
            return result.one()

    @staticmethod
    async def list_by_user(
        mid: int | None,
        skip: int = 0,
        limit: int = 100,
        filter_type: str = "all",
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        browser_id: int | None = None,
    ) -> List[UserWorkflow]:
        from sqlmodel import col

        async with DatabaseSessionManager.async_session() as session:
            query = select(UserWorkflow)
            query = WorkflowCrudService._apply_scope_filter(query, mid, filter_type)
            if browser_id is not None:
                query = query.where(UserWorkflow.browser_id == browser_id)

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
        notify_inbox_on_start: bool | None = None,
        notify_inbox_on_end: bool | None = None,
        notify_push_on_start: bool | None = None,
        notify_push_on_end: bool | None = None,
        log_enabled: bool | None = None,
        log_debug: bool | None = None,
        auto_sync_upstream: bool | None = None,
        resume_on_restart: bool | None = None,
        browser_ids: list[int] | None = None,
        concurrent_exec: bool | None = None,
        variables: Dict | None = None,
        variables_patch: Dict | None = None,
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
                # 触发配置属于会被同步覆盖的内容：本地修改后自动同步跳过
                # （docs/fork-上游同步计划书.md）
                model.local_steps_modified = True
            if trigger_config is not None:
                model.trigger_config = trigger_config
                model.local_steps_modified = True
            if is_enabled is not None:
                model.is_enabled = is_enabled
            if is_public is not None:
                model.is_public = is_public
            if notify_inbox_on_start is not None:
                model.notify_inbox_on_start = notify_inbox_on_start
            if notify_inbox_on_end is not None:
                model.notify_inbox_on_end = notify_inbox_on_end
            if notify_push_on_start is not None:
                model.notify_push_on_start = notify_push_on_start
            if notify_push_on_end is not None:
                model.notify_push_on_end = notify_push_on_end
            # 旧字段保持为派生值（唯一来源是 4 个新开关），旧读方口径不失真
            model.notify_on_start = bool(model.notify_inbox_on_start) or bool(
                model.notify_push_on_start
            )
            model.notify_on_end = bool(model.notify_inbox_on_end) or bool(
                model.notify_push_on_end
            )
            if log_enabled is not None:
                model.log_enabled = log_enabled
            if log_debug is not None:
                model.log_debug = log_debug
            if resume_on_restart is not None:
                model.resume_on_restart = resume_on_restart
            if auto_sync_upstream is not None:
                model.auto_sync_upstream = auto_sync_upstream
            if browser_ids is not None:
                # 空列表显式回到单浏览器模式（browser_id 保留为主浏览器）
                model.browser_ids = list(browser_ids) if browser_ids else None
            if concurrent_exec is not None:
                model.concurrent_exec = concurrent_exec
            if variables is not None:
                # 整体替换（前端按动作契约渲染全量值），不做增量合并，
                # 否则被删掉的变量会一直残留
                model.variables = variables
            elif variables_patch:
                # 增量更新：只改给出的键（null=删除），避免请求体不完整时清空全部变量
                model.variables = merge_variables_patch(
                    model.variables, variables_patch
                )

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
            notify_flags = NotifyChannelFlags.from_model(original)
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
                notify_inbox_on_start=notify_flags.inbox_on_start,
                notify_inbox_on_end=notify_flags.inbox_on_end,
                notify_push_on_start=notify_flags.push_on_start,
                notify_push_on_end=notify_flags.push_on_end,
                notify_on_start=notify_flags.legacy_on_start,
                notify_on_end=notify_flags.legacy_on_end,
                log_enabled=original.log_enabled,
                log_debug=original.log_debug,
                resume_on_restart=original.resume_on_restart,
                # 副本仍属同一用户：变量值一并复制，避免重新填一遍
                variables=dict(original.variables or {}),
            )

            session.add(new_model)
            await session.commit()
            await session.refresh(new_model)
            return new_model

    @staticmethod
    async def fork(
        id: int,
        target_mid: int,
        new_name: str | None = None,
        auto_sync_upstream: bool = False,
    ) -> tuple[UserWorkflow | None, int]:
        """Fork 工作流，并递归深拷贝其引用的 Action 依赖树

        - 顶层 Action 如果属于他人，会通过 fork_deep 复制整棵第三方依赖树
        - 顶层 Action 如果已属于自己，直接保留引用
        - 浏览器是私有资源不继承

        Args:
            id: 上游工作流数据库 ID
            target_mid: Fork 目标用户 mid
            new_name: 新工作流名称（可选）
            auto_sync_upstream: 是否开启上游自动同步（外壳与顶层 Action 同时生效）

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
            original_notify_flags = NotifyChannelFlags.from_model(original)
            original_log_enabled = original.log_enabled
            original_log_debug = original.log_debug
            original_updated_at = original.updated_at

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
                    auto_sync_upstream=auto_sync_upstream,
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
                    (UserWorkflow.mid == str(target_mid))
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
                # 上游同步快照：记录"此副本同步自上游哪个版本"（docs/fork-上游同步计划书.md）
                last_synced_at=original_updated_at,
                auto_sync_upstream=auto_sync_upstream,
                local_steps_modified=False,
                notify_inbox_on_start=original_notify_flags.inbox_on_start,
                notify_inbox_on_end=original_notify_flags.inbox_on_end,
                notify_push_on_start=original_notify_flags.push_on_start,
                notify_push_on_end=original_notify_flags.push_on_end,
                notify_on_start=original_notify_flags.legacy_on_start,
                notify_on_end=original_notify_flags.legacy_on_end,
                log_enabled=original_log_enabled,
                log_debug=original_log_debug,
                resume_on_restart=bool(original.resume_on_restart),
                # 变量值是原作者的配置（可能含 token 等敏感值），Fork 给他人不继承，
                # 由 Fork 方按动作契约自行填写（定义仍从动作读时获取）。
                variables={},
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
            同步结果 dict；工作流不存在返回 None。
            local_overwritten: 同步是否覆盖了本地修改（外壳触发配置或 Action 内容）
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
                    "local_overwritten": False,
                }

            # 查找上游工作流
            upstream_result = await session.exec(
                select(UserWorkflow).where(
                    UserWorkflow.id == workflow.forked_from_id
                )
            )
            upstream_wf = upstream_result.first()

            workflow_config_updated = False
            local_overwritten = False
            if upstream_wf:
                # 本地修改保护：同步前捕获外壳配置是否被用户改过
                if workflow.local_steps_modified:
                    local_overwritten = True
                # 同步触发配置（不同步 browser_id，那是私有资源）
                if upstream_wf.trigger_config != workflow.trigger_config:
                    workflow.trigger_config = upstream_wf.trigger_config
                    workflow_config_updated = True
                if upstream_wf.trigger_type != workflow.trigger_type:
                    workflow.trigger_type = upstream_wf.trigger_type
                    workflow_config_updated = True
                workflow.updated_at = datetime.now()
                # 同步快照前移：外壳已与上游当前版本一致（docs/fork-上游同步计划书.md）
                workflow.last_synced_at = upstream_wf.updated_at
                workflow.local_steps_modified = False
                session.add(workflow)

            await session.commit()

            # 同步 Action 依赖树
            action_result = {
                "updated": [],
                "created": [],
                "unavailable": [],
                "local_overwritten": False,
            }
            if workflow.custom_action_id:
                action_result = await action_crud_svr.sync_from_upstream(
                    action_id=workflow.custom_action_id,
                    mid=mid,
                )

            if action_result.get("local_overwritten"):
                local_overwritten = True

            return {
                "workflow_id": workflow.workflow_id,
                "updated": action_result.get("updated", []),
                "created": action_result.get("created", []),
                "unavailable": action_result.get("unavailable", []),
                "workflow_config_updated": workflow_config_updated,
                "local_overwritten": local_overwritten,
            }

    # ═══════════════ 上游同步状态（批量标注） ═══════════════

    @staticmethod
    async def attach_upstream_update_flags(
        models: List[UserWorkflow],
    ) -> Dict[int, UpstreamSyncFlag]:
        """批量计算每个工作流的「是否需要同步上游」状态

        一次 IN 查询取回所有上游的 updated_at / is_public，避免 N+1。

        need_sync 判定（docs/fork-上游同步计划书.md §4.1）：
        forked_from_id 非空 且上游存在且公开 且
        上游.updated_at > last_synced_at（last_synced_at 为 None 视为需要同步）。
        上游已删除/私有化：不提示同步（保留当前副本）。

        Args:
            models: 待标注的工作流列表（通常是列表接口的一页结果）

        Returns:
            {model.id: UpstreamSyncFlag}；非 Fork 资源不出现在结果中
        """
        flags: Dict[int, UpstreamSyncFlag] = {}
        fork_models = [m for m in models if m.forked_from_id]
        if not fork_models:
            return flags

        upstream_ids = list({m.forked_from_id for m in fork_models})
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(
                    UserWorkflow.id,
                    UserWorkflow.updated_at,
                    UserWorkflow.is_public,
                ).where(UserWorkflow.id.in_(upstream_ids))
            )
            upstream_map: Dict[int, tuple[datetime, bool]] = {
                row[0]: (row[1], bool(row[2])) for row in result.all()
            }

        for m in fork_models:
            assert m.id is not None
            upstream = upstream_map.get(m.forked_from_id)
            if not upstream or not upstream[1]:
                # 上游已删除或私有化：不提示同步
                flags[m.id] = UpstreamSyncFlag(
                    has_upstream_update=False, upstream_updated_at=None
                )
                continue
            upstream_updated_at = upstream[0]
            has_update = (
                m.last_synced_at is None or upstream_updated_at > m.last_synced_at
            )
            flags[m.id] = UpstreamSyncFlag(
                has_upstream_update=has_update,
                upstream_updated_at=int(upstream_updated_at.timestamp()),
            )
        return flags

    # ── 管理端（root）全量查询 ──────────────────────────────

    @staticmethod
    def _apply_admin_filters(
        query,
        mid: int | None = None,
        name: str | None = None,
        is_public: bool | None = None,
        is_enabled: bool | None = None,
    ):
        """按管理端筛选条件收敛查询（不带任何默认 mid 归属限制）"""
        from sqlmodel import col

        if mid is not None:
            query = query.where(UserWorkflow.mid == str(mid))
        if name:
            query = query.where(col(UserWorkflow.name).like(f"%{name.strip()}%"))
        if is_public is not None:
            query = query.where(UserWorkflow.is_public == is_public)
        if is_enabled is not None:
            query = query.where(UserWorkflow.is_enabled == is_enabled)
        return query

    @staticmethod
    async def count_all(
        mid: int | None = None,
        name: str | None = None,
        is_public: bool | None = None,
        is_enabled: bool | None = None,
    ) -> int:
        """统计全部用户的工作流数量（管理端全量视图）"""
        from sqlmodel import func

        async with DatabaseSessionManager.async_session() as session:
            query = select(func.count(UserWorkflow.id))
            query = WorkflowCrudService._apply_admin_filters(
                query, mid, name, is_public, is_enabled
            )
            return (await session.exec(query)).one()

    @staticmethod
    async def list_all(
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        mid: int | None = None,
        name: str | None = None,
        is_public: bool | None = None,
        is_enabled: bool | None = None,
    ) -> List[UserWorkflow]:
        """列出全部用户的工作流（管理端全量视图，跨作者、支持筛选与排序）"""
        from sqlmodel import col

        async with DatabaseSessionManager.async_session() as session:
            query = select(UserWorkflow)
            query = WorkflowCrudService._apply_admin_filters(
                query, mid, name, is_public, is_enabled
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


# 单例：供包内 __init__ 与其他模块直接导入使用
workflow_crud_svr = WorkflowCrudService()
      