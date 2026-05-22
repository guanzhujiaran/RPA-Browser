"""
执行模块 CRUD 服务

提供操作、插件、工作流的完整 CRUD 功能
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import uuid
from sqlmodel import select, update

from app.models.database.workflow.models import (
    CustomAction,
    UserPlugin,
    UserWorkflow,
    WorkflowExecutionLog,
    WorkflowPluginRelation,
    ActionPluginRelation,
    ActionType,
    ResourceLike,
    ResourceReport,
    ResourceType,
)
from app.models.exceptions.base_exception import NameAlreadyExistsException
from app.utils.depends.session_manager import DatabaseSessionManager


class ActionCrudService:
    """操作 CRUD 服务"""

    @staticmethod
    async def create(
        mid: str,
        action_id: str,
        name: str,
        action_type: ActionType = ActionType.COMPOSITE,
        parameters_schema: List[Dict[str, Any]] = None,
        steps: List[Dict[str, Any]] = None,
        is_composite: bool = False,
        description: str = "",
        tags: Optional[List[str]] = None,
        user_data: Optional[Dict[str, Any]] = None,
        is_public: bool = False,
        enabled_plugins: List[Dict[str, Any]] = None, # [{"plugin_id": "...", "config_params": {...}}]
        timeout: int = 30000,
    ) -> CustomAction:
        """创建自定义操作（仅支持预定义动作组合）
        
        Raises:
            ValueError: 如果同一用户下已存在同名操作
        """
        async with DatabaseSessionManager.async_session() as session:
            # 检查同一用户下是否已存在同名操作
            existing = await session.exec(
                select(CustomAction).where(
                    (CustomAction.mid == mid) & (CustomAction.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="操作")
            
            model = CustomAction(
                action_id=action_id,
                name=name,
                action_type=action_type,
                description=description,
                mid=mid,
                timeout=timeout,
                is_composite=is_composite,
                parameters_schema=parameters_schema or [],
                steps=steps or [],
                tags=tags or [],
                user_data=user_data,
                is_public=is_public,
            )
            session.add(model)
            
            # 处理插件关联
            if enabled_plugins:
                for link_data in enabled_plugins:
                    link = ActionPluginRelation(
                        action_id=action_id,
                        plugin_id=link_data.get("plugin_id"),
                        config_params=link_data.get("config_params", {})
                    )
                    session.add(link)
            
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def get_by_id(id: int) -> Optional[CustomAction]:
        """根据ID获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomAction).where(CustomAction.id == id))
            return result.first()

    @staticmethod
    async def get_enabled_plugins(action_id: str) -> List[Dict[str, Any]]:
        """获取操作关联的插件列表"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ActionPluginRelation).where(ActionPluginRelation.action_id == action_id)
            )
            links = result.all()
            return [
                {
                    "plugin_id": link.plugin_id,
                    "config_params": link.config_params or {}
                }
                for link in links
            ]

    @staticmethod
    async def count_by_user(
        mid: str,
        filter_type: str = "all",
    ) -> int:
        """获取用户的操作总数
        
        Args:
            mid: 用户ID
            filter_type: 筛选类型 (all, private, public, community, verified)
        """
        from sqlmodel import func
        
        async with DatabaseSessionManager.async_session() as session:
            # 构建查询条件
            query = select(func.count(CustomAction.id))
            
            # 应用筛选条件
            if filter_type == "private":
                query = query.where(
                    (CustomAction.mid == mid) & (CustomAction.is_public == False)
                )
            elif filter_type == "public":
                query = query.where(
                    (CustomAction.mid == mid) & (CustomAction.is_public == True)
                )
            elif filter_type == "community":
                query = query.where(
                    (CustomAction.mid != mid) & (CustomAction.is_public == True)
                )
            elif filter_type == "verified":
                query = query.where(CustomAction.is_verified == True)
            else:
                query = query.where(
                    (CustomAction.mid == mid) | (CustomAction.is_public == True)
                )
            
            result = await session.exec(query)
            return result.one()

    @staticmethod
    async def list_by_user(
        mid: str, 
        skip: int = 0, 
        limit: int = 100,
        filter_type: str = "all",
        sort_by: str = "updated_at",
        sort_order: str = "desc"
    ) -> List[CustomAction]:
        """获取用户的所有操作（包含公开的）
        
        Args:
            mid: 用户ID
            skip: 跳过记录数
            limit: 返回记录数
            filter_type: 筛选类型 (all, private, public, community, verified)
            sort_by: 排序字段 (updated_at, likes_count, forks_count, created_at, name)
            sort_order: 排序方向 (desc, asc)
        """
        from sqlmodel import col
        
        async with DatabaseSessionManager.async_session() as session:
            # 构建查询条件
            query = select(CustomAction)
            
            # 应用筛选条件
            if filter_type == "private":
                # 我的私有：当前用户的且未公开
                query = query.where(
                    (CustomAction.mid == mid) & (CustomAction.is_public == False)
                )
            elif filter_type == "public":
                # 我的公开：当前用户的且已公开
                query = query.where(
                    (CustomAction.mid == mid) & (CustomAction.is_public == True)
                )
            elif filter_type == "community":
                # 社区公开：非当前用户的且已公开
                query = query.where(
                    (CustomAction.mid != mid) & (CustomAction.is_public == True)
                )
            elif filter_type == "verified":
                # 已认证：所有已认证的
                query = query.where(CustomAction.is_verified == True)
            else:
                # all: 查询属于该用户的 OR 公开给所有人的
                query = query.where(
                    (CustomAction.mid == mid) | (CustomAction.is_public == True)
                )
            
            # 应用排序
            sort_field = getattr(CustomAction, sort_by, CustomAction.updated_at)
            if sort_order == "asc":
                query = query.order_by(col(sort_field).asc())
            else:
                query = query.order_by(col(sort_field).desc())
            
            # 应用分页
            query = query.offset(skip).limit(limit)
            
            result = await session.exec(query)
            return result.all()

    @staticmethod
    async def update(
        id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        parameters_schema: Optional[List[Dict[str, Any]]] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        tags: Optional[List[str]] = None,
        user_data: Optional[Dict[str, Any]] = None,
        is_composite: Optional[bool] = None,
        timeout: Optional[int] = None,
        is_public: Optional[bool] = None,
        enabled_plugins: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[CustomAction]:
        """更新操作（仅支持预定义动作组合）
        
        Raises:
            ValueError: 如果同一用户下已存在同名操作
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomAction).where(CustomAction.id == id))
            model = result.first()
            if not model:
                return None

            # 如果要更新名称，检查是否与同一用户的其他操作重名
            if name is not None and name != model.name:
                existing = await session.exec(
                    select(CustomAction).where(
                        (CustomAction.mid == model.mid) & 
                        (CustomAction.name == name) &
                        (CustomAction.id != id)
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
            if user_data is not None:
                model.user_data = user_data
            if is_composite is not None:
                model.is_composite = is_composite
            if timeout is not None:
                model.timeout = timeout
            if is_public is not None:
                model.is_public = is_public
            
            # 更新插件关联：先删后增
            if enabled_plugins is not None:
                old_links = await session.exec(
                    select(ActionPluginRelation).where(ActionPluginRelation.action_id == model.action_id)
                )
                for link in old_links.all():
                    await session.delete(link)
                
                for link_data in enabled_plugins:
                    link = ActionPluginRelation(
                        action_id=model.action_id,
                        plugin_id=link_data.get("plugin_id"),
                        config_params=link_data.get("config_params", {})
                    )
                    session.add(link)

            model.updated_at = datetime.now()
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def delete(id: int) -> bool:
        """删除操作（同时删除关联的插件记录）
        
        如果删除的是 Fork 版本，会减少原资源的 forks_count
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomAction).where(CustomAction.id == id))
            model = result.first()
            if not model:
                return False
            
            # 如果是 Fork 版本，减少原资源的 Fork 计数
            if model.forked_from_id:
                await session.exec(
                    update(CustomAction)
                    .where(CustomAction.id == model.forked_from_id)
                    .values(forks_count=CustomAction.forks_count - 1)
                )
            
            # 先删除关联的插件记录
            links = await session.exec(
                select(ActionPluginRelation).where(ActionPluginRelation.action_id == model.action_id)
            )
            for link in links.all():
                await session.delete(link)
            
            # 再删除操作本身
            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        """启用操作"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomAction).where(CustomAction.id == id))
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
            result = await session.exec(select(CustomAction).where(CustomAction.id == id))
            model = result.first()
            if not model:
                return False
            model.is_enabled = False
            model.updated_at = datetime.now()
            await session.commit()
            return True

    @staticmethod
    async def increment_likes(id: int) -> bool:
        """增加点赞数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(CustomAction)
                .where(CustomAction.id == id)
                .values(likes_count=CustomAction.likes_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        """增加举报数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(CustomAction)
                .where(CustomAction.id == id)
                .values(reports_count=CustomAction.reports_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def list_forks(action_id: int, skip: int = 0, limit: int = 50) -> List[CustomAction]:
        """获取某操作的所有 Fork 版本"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CustomAction)
                .where(CustomAction.forked_from_id == action_id)
                .order_by(CustomAction.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def fork(id: int, target_mid: str, new_name: str | None = None) -> Optional[CustomAction]:
        """Fork 自定义操作（仅允许 Fork 公开的操作）
        
        Args:
            id: 原操作ID
            target_mid: 目标用户ID
            new_name: 新名称，如果不提供则使用原名称 + ' (Fork)'
        
        Returns:
            Fork 后的操作模型
            
        Raises:
            ValueError: 如果原操作不是公开的
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomAction).where(CustomAction.id == id))
            original = result.first()
            if not original:
                return None
            
            # 只允许 Fork 公开的操作
            if not original.is_public:
                raise ValueError("只能 Fork 公开的操作")

            # 生成新的 action_id（格式：ca_xxx）
            new_action_id = f"ca_{uuid.uuid4().hex[:12]}"
            
            # 如果未提供新名称，使用默认命名
            if not new_name:
                new_name = f"{original.name} (Fork)"
            
            # 检查目标用户下是否已存在同名操作
            existing = await session.exec(
                select(CustomAction).where(
                    (CustomAction.mid == target_mid) & (CustomAction.name == new_name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=new_name, name_type="操作")

            new_model = CustomAction(
                action_id=new_action_id,
                name=new_name,
                action_type=original.action_type,
                description=f"Forked from: {original.name}",
                mid=target_mid,  # 设置为目标用户
                timeout=original.timeout,
                is_composite=original.is_composite,
                parameters_schema=original.parameters_schema.copy() if original.parameters_schema else [],
                steps=original.steps.copy() if original.steps else [],
                tags=original.tags.copy() if original.tags else [],
                user_data=original.user_data.copy() if original.user_data else None,
                is_public=False,  # Fork 后默认为私有
                forked_from_id=original.id,  # 记录来源
            )

            session.add(new_model)
            
            # 增加原资源的 Fork 计数
            await session.exec(
                update(CustomAction)
                .where(CustomAction.id == original.id)
                .values(forks_count=CustomAction.forks_count + 1)
            )
            
            await session.commit()
            await session.refresh(new_model)
            return new_model


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
        user_data: Optional[Dict[str, Any]] = None,
        is_public: bool = False,
        enabled_plugins: List[Dict[str, Any]] = None, # [{"plugin_id": "...", "config_params": {...}}]
    ) -> UserWorkflow:
        """创建工作流
        
        Raises:
            ValueError: 如果同一用户下已存在同名工作流
        """
        async with DatabaseSessionManager.async_session() as session:
            # 检查同一用户下是否已存在同名工作流
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
                description=description,
                on_error=on_error,
                mid=mid,
                steps=steps,
                tags=tags or [],
                user_data=user_data,
                is_public=is_public,
            )
            session.add(model)
            
            # 处理插件关联
            if enabled_plugins:
                for link_data in enabled_plugins:
                    link = WorkflowPluginRelation(
                        workflow_id=workflow_id,
                        plugin_id=link_data.get("plugin_id"),
                        config_params=link_data.get("config_params", {})
                    )
                    session.add(link)
            
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def get_by_id(id: int) -> Optional[UserWorkflow]:
        """根据ID获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            return result.first()

    @staticmethod
    async def get_enabled_plugins(workflow_id: str) -> List[Dict[str, Any]]:
        """获取工作流关联的插件列表
        
        Returns:
            List[Dict]: 包含 plugin_id 和 config_params 的列表
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowPluginRelation).where(WorkflowPluginRelation.workflow_id == workflow_id)
            )
            links = result.all()
            return [
                {
                    "plugin_id": link.plugin_id,
                    "config_params": link.config_params or {}
                }
                for link in links
            ]

    @staticmethod
    async def count_by_user(
        mid: str,
        filter_type: str = "all",
    ) -> int:
        """获取用户的工作流总数
        
        Args:
            mid: 用户ID
            filter_type: 筛选类型 (all, private, public, community, verified)
        """
        from sqlmodel import func
        
        async with DatabaseSessionManager.async_session() as session:
            # 构建查询条件
            query = select(func.count(UserWorkflow.id))
            
            # 应用筛选条件
            if filter_type == "private":
                query = query.where(
                    (UserWorkflow.mid == mid) & (UserWorkflow.is_public == False)
                )
            elif filter_type == "public":
                query = query.where(
                    (UserWorkflow.mid == mid) & (UserWorkflow.is_public == True)
                )
            elif filter_type == "community":
                query = query.where(
                    (UserWorkflow.mid != mid) & (UserWorkflow.is_public == True)
                )
            elif filter_type == "verified":
                query = query.where(UserWorkflow.is_verified == True)
            else:
                query = query.where(
                    (UserWorkflow.mid == mid) | (UserWorkflow.is_public == True)
                )
            
            result = await session.exec(query)
            return result.one()

    @staticmethod
    async def list_by_user(
        mid: str, 
        skip: int = 0, 
        limit: int = 100, 
        include_public: bool = True,
        filter_type: str = "all",
        sort_by: str = "updated_at",
        sort_order: str = "desc"
    ) -> List[UserWorkflow]:
        """获取用户的所有工作流（可选择包含公开资源）
        
        Args:
            mid: 用户ID
            skip: 跳过记录数
            limit: 返回记录数
            include_public: 是否包含公开资源
            filter_type: 筛选类型 (all, private, public, community, verified)
            sort_by: 排序字段 (updated_at, likes_count, forks_count, created_at, name)
            sort_order: 排序方向 (desc, asc)
        """
        from sqlmodel import col
        
        async with DatabaseSessionManager.async_session() as session:
            query = select(UserWorkflow)
            
            # 应用筛选条件
            if filter_type == "private":
                # 我的私有：当前用户的且未公开
                query = query.where(
                    (UserWorkflow.mid == mid) & (UserWorkflow.is_public == False)
                )
            elif filter_type == "public":
                # 我的公开：当前用户的且已公开
                query = query.where(
                    (UserWorkflow.mid == mid) & (UserWorkflow.is_public == True)
                )
            elif filter_type == "community":
                # 社区公开：非当前用户的且已公开
                query = query.where(
                    (UserWorkflow.mid != mid) & (UserWorkflow.is_public == True)
                )
            elif filter_type == "verified":
                # 已认证：所有已认证的
                query = query.where(UserWorkflow.is_verified == True)
            else:
                # all: 根据include_public参数决定
                if include_public:
                    query = query.where((UserWorkflow.mid == mid) | (UserWorkflow.is_public == True))
                else:
                    query = query.where(UserWorkflow.mid == mid)
            
            # 应用排序
            sort_field = getattr(UserWorkflow, sort_by, UserWorkflow.updated_at)
            if sort_order == "asc":
                query = query.order_by(col(sort_field).asc())
            else:
                query = query.order_by(col(sort_field).desc())
            
            # 应用分页
            result = await session.exec(query.offset(skip).limit(limit))
            return result.all()

    @staticmethod
    async def update(
        id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        on_error: Optional[str] = None,
        tags: Optional[List[str]] = None,
        user_data: Optional[Dict[str, Any]] = None,
        is_public: Optional[bool] = None,
        enabled_plugins: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[UserWorkflow]:
        """更新工作流
        
        Raises:
            ValueError: 如果同一用户下已存在同名工作流
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            model = result.first()
            if not model:
                return None

            # 如果要更新名称，检查是否与同一用户的其他工作流重名
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
            if steps is not None:
                model.steps = steps
            if on_error is not None:
                model.on_error = on_error
            if tags is not None:
                model.tags = tags
            if user_data is not None:
                model.user_data = user_data
            if is_public is not None:
                model.is_public = is_public

            # 更新插件关联：先删后增
            if enabled_plugins is not None:
                # 删除旧的关联
                old_links = await session.exec(
                    select(WorkflowPluginRelation).where(WorkflowPluginRelation.workflow_id == model.workflow_id)
                )
                for link in old_links.all():
                    await session.delete(link)
                
                # 增加新的关联
                for link_data in enabled_plugins:
                    link = WorkflowPluginRelation(
                        workflow_id=model.workflow_id,
                        plugin_id=link_data.get("plugin_id"),
                        config_params=link_data.get("config_params", {})
                    )
                    session.add(link)

            model.updated_at = datetime.now()
            await session.commit()
            await session.refresh(model)
            return model

    @staticmethod
    async def delete(id: int) -> bool:
        """删除工作流（同时删除关联的插件记录）
        
        如果删除的是 Fork 版本，会减少原资源的 forks_count
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            model = result.first()
            if not model:
                return False
            
            # 如果是 Fork 版本，减少原资源的 Fork 计数
            if model.forked_from_id:
                await session.exec(
                    update(UserWorkflow)
                    .where(UserWorkflow.id == model.forked_from_id)
                    .values(forks_count=UserWorkflow.forks_count - 1)
                )
            
            # 先删除关联的插件记录
            links = await session.exec(
                select(WorkflowPluginRelation).where(WorkflowPluginRelation.workflow_id == model.workflow_id)
            )
            for link in links.all():
                await session.delete(link)
            
            # 再删除工作流本身
            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        """启用工作流"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
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
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            model = result.first()
            if not model:
                return False
            model.is_enabled = False
            model.updated_at = datetime.now()
            await session.commit()
            return True

    @staticmethod
    async def duplicate(id: int) -> Optional[UserWorkflow]:
        """复制工作流（同一用户）"""
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
                on_error=original.on_error,
                steps=original.steps,
                tags=original.tags,
                user_data=original.user_data,
                is_public=False, # 克隆后默认为私有
            )

            session.add(new_model)
            await session.commit()
            await session.refresh(new_model)
            return new_model

    @staticmethod
    async def fork(id: int, target_mid: str, new_name: str | None = None) -> Optional[UserWorkflow]:
        """Fork 工作流（仅允许 Fork 公开的工作流）
        
        Args:
            id: 原工作流ID
            target_mid: 目标用户ID
            new_name: 新名称，如果不提供则使用原名称 + ' (Fork)'
        
        Returns:
            Fork 后的工作流模型
            
        Raises:
            ValueError: 如果原工作流不是公开的
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflow).where(UserWorkflow.id == id))
            original = result.first()
            if not original:
                return None
            
            # 只允许 Fork 公开的工作流
            if not original.is_public:
                raise ValueError("只能 Fork 公开的工作流")

            # 生成新的 workflow_id
            new_workflow_id = str(uuid.uuid4())
            
            # 如果未提供新名称，使用默认命名
            if not new_name:
                new_name = f"{original.name} (Fork)"
            
            # 检查目标用户下是否已存在同名工作流
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
                mid=target_mid,  # 设置为目标用户
                on_error=original.on_error,
                steps=original.steps.copy(),  # 深拷贝步骤
                tags=original.tags.copy() if original.tags else [],
                user_data=original.user_data.copy() if original.user_data else None,
                is_public=False,  # Fork 后默认为私有
                forked_from_id=original.id,  # 记录来源
            )

            session.add(new_model)
            
            # 增加原资源的 Fork 计数
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
        """增加点赞数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(UserWorkflow)
                .where(UserWorkflow.id == id)
                .values(likes_count=UserWorkflow.likes_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        """增加举报数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(UserWorkflow)
                .where(UserWorkflow.id == id)
                .values(reports_count=UserWorkflow.reports_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def list_forks(workflow_id: int, skip: int = 0, limit: int = 50) -> List[UserWorkflow]:
        """获取某工作流的所有 Fork 版本"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflow)
                .where(UserWorkflow.forked_from_id == workflow_id)
                .order_by(UserWorkflow.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()


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
    ) -> WorkflowExecutionLog:
        """创建执行日志"""
        async with DatabaseSessionManager.async_session() as session:
            success_count = sum(1 for r in results if r.get("success"))
            failed_count = len(results) - success_count

            model = WorkflowExecutionLog(
                workflow_id=workflow_id,
                session_id=session_id,
                browser_id=browser_id,
                mid=mid,
                execution_id=execution_id,
                status=status,
                steps_count=len(results),
                success_count=success_count,
                failed_count=failed_count,
                results=results,
            )
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
                select(WorkflowExecutionLog)
                .where(WorkflowExecutionLog.execution_id == execution_id)
            )
            model = result.first()
            if not model:
                return False

            model.status = status
            model.total_time = total_time
            if results is not None:
                model.results = results
                model.success_count = sum(1 for r in results if r.get("success"))
                model.failed_count = len(results) - model.success_count

            if status in ("success", "failed", "timeout", "cancelled"):
                model.finished_at = datetime.now()

            await session.commit()
            return True

    @staticmethod
    async def get_by_execution_id(execution_id: str) -> Optional[WorkflowExecutionLog]:
        """根据执行ID获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLog)
                .where(WorkflowExecutionLog.execution_id == execution_id)
            )
            return result.first()

    @staticmethod
    async def list_by_workflow(
        workflow_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> List[WorkflowExecutionLog]:
        """获取工作流的所有执行记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLog)
                .where(WorkflowExecutionLog.workflow_id == workflow_id)
                .order_by(WorkflowExecutionLog.started_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def list_by_user(
        mid: str,
        skip: int = 0,
        limit: int = 100,
    ) -> List[WorkflowExecutionLog]:
        """获取用户的所有执行记录"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowExecutionLog)
                .where(WorkflowExecutionLog.mid == mid)
                .order_by(WorkflowExecutionLog.started_at.desc())
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
                select(WorkflowExecutionLog)
                .where(WorkflowExecutionLog.started_at < cutoff)
            )
            models = result.all()
            count = len(models)
            for model in models:
                await session.delete(model)
            await session.commit()
            return count


class PluginCrudService:
    """插件挂载 CRUD 服务"""

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
        """创建插件挂载配置
        
        Raises:
            ValueError: 如果同一用户下已存在同名插件
            ValueError: 如果钩子类型无效
            ValueError: 如果自定义动作不存在或不可用
        """
        async with DatabaseSessionManager.async_session() as session:
            # 1. 验证钩子类型是否有效
            from app.models.core.workflow.models import PluginHookEnum
            valid_hook_types = [hook.value for hook in PluginHookEnum]
            if hook_type not in valid_hook_types:
                raise ValueError(
                    f"无效的钩子类型 '{hook_type}'。"
                    f"有效的钩子类型包括: {', '.join(valid_hook_types)}"
                )
            
            # 2. 验证自定义动作是否存在且可用
            action_result = await session.exec(
                select(CustomAction).where(
                    (CustomAction.action_id == custom_action_id) &
                    (CustomAction.is_enabled == True)
                )
            )
            action_model = action_result.first()
            if not action_model:
                raise ValueError(
                    f"自定义动作 '{custom_action_id}' 不存在或已被禁用。"
                    f"请确保该动作存在且处于启用状态。"
                )
            
            # 3. 检查权限：如果是私有动作，必须是所有者
            if not action_model.is_public and action_model.mid != mid:
                raise ValueError(
                    f"无权使用私有的自定义动作 '{custom_action_id}'。"
                    f"请联系动作所有者将其公开，或使用您自己的动作。"
                )
            
            # 4. 检查同一用户下是否已存在同名插件
            existing = await session.exec(
                select(UserPlugin).where(
                    (UserPlugin.mid == mid) & (UserPlugin.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="插件")
            
            # 5. 创建插件配置
            model = UserPlugin(
                mid=mid,
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
    async def get_by_id(id: int) -> Optional[UserPlugin]:
        """根据ID获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPlugin).where(UserPlugin.id == id))
            return result.first()

    @staticmethod
    async def get_by_plugin_id(plugin_id: str) -> Optional[UserPlugin]:
        """根据 plugin_id 获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserPlugin).where(UserPlugin.plugin_id == plugin_id)
            )
            return result.first()

    @staticmethod
    async def get_by_ids(ids: List[str]) -> List[UserPlugin]:
        """根据ID列表批量获取插件详情"""
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
    async def count_by_user(
        mid: int,
        filter_type: str = "all",
    ) -> int:
        """获取用户的插件总数
        
        Args:
            mid: 用户ID
            filter_type: 筛选类型 (all, private, public, community, verified)
        """
        from sqlmodel import func
        
        async with DatabaseSessionManager.async_session() as session:
            # 构建查询条件
            query = select(func.count(UserPlugin.id))
            
            # 应用筛选条件
            if filter_type == "private":
                query = query.where(
                    (UserPlugin.mid == mid) & (UserPlugin.is_public == False)
                )
            elif filter_type == "public":
                query = query.where(
                    (UserPlugin.mid == mid) & (UserPlugin.is_public == True)
                )
            elif filter_type == "community":
                query = query.where(
                    (UserPlugin.mid != mid) & (UserPlugin.is_public == True)
                )
            elif filter_type == "verified":
                query = query.where(UserPlugin.is_verified == True)
            else:
                query = query.where(
                    (UserPlugin.mid == mid) | (UserPlugin.is_public == True)
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
        sort_order: str = "desc"
    ) -> List[UserPlugin]:
        """获取用户的插件配置（包含公开的）
        
        Args:
            mid: 用户ID
            skip: 跳过记录数
            limit: 返回记录数
            filter_type: 筛选类型 (all, private, public, community, verified)
            sort_by: 排序字段 (updated_at, likes_count, forks_count, created_at, name)
            sort_order: 排序方向 (desc, asc)
        """
        from sqlmodel import col
        
        async with DatabaseSessionManager.async_session() as session:
            query = select(UserPlugin)
            
            # 应用筛选条件
            if filter_type == "private":
                # 我的私有：当前用户的且未公开
                query = query.where(
                    (UserPlugin.mid == mid) & (UserPlugin.is_public == False)
                )
            elif filter_type == "public":
                # 我的公开：当前用户的且已公开
                query = query.where(
                    (UserPlugin.mid == mid) & (UserPlugin.is_public == True)
                )
            elif filter_type == "community":
                # 社区公开：非当前用户的且已公开
                query = query.where(
                    (UserPlugin.mid != mid) & (UserPlugin.is_public == True)
                )
            elif filter_type == "verified":
                # 已认证：所有已认证的
                query = query.where(UserPlugin.is_verified == True)
            else:
                # all: 查询属于该用户的 OR 公开给所有人的
                query = query.where(
                    (UserPlugin.mid == mid) | (UserPlugin.is_public == True)
                )
            
            # 应用排序
            sort_field = getattr(UserPlugin, sort_by, UserPlugin.updated_at)
            if sort_order == "asc":
                query = query.order_by(col(sort_field).asc())
            else:
                query = query.order_by(col(sort_field).desc())
            
            # 应用分页
            result = await session.exec(query.offset(skip).limit(limit))
            return result.all()

    @staticmethod
    async def update(
        id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        hook_type: Optional[str] = None,
        custom_action_id: Optional[str] = None,
        priority: Optional[int] = None,
        is_public: Optional[bool] = None,
    ) -> Optional[UserPlugin]:
        """更新插件配置
        
        Raises:
            ValueError: 如果同一用户下已存在同名插件
            ValueError: 如果钩子类型无效
            ValueError: 如果自定义动作不存在或不可用
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPlugin).where(UserPlugin.id == id))
            model = result.first()
            if not model:
                return None

            # 1. 如果要更新钩子类型，验证其有效性
            if hook_type is not None and hook_type != model.hook_type:
                from app.models.core.workflow.models import PluginHookEnum
                valid_hook_types = [hook.value for hook in PluginHookEnum]
                if hook_type not in valid_hook_types:
                    raise ValueError(
                        f"无效的钩子类型 '{hook_type}'。"
                        f"有效的钩子类型包括: {', '.join(valid_hook_types)}"
                    )
                model.hook_type = hook_type
            
            # 2. 如果要更新自定义动作，验证其存在性和权限
            if custom_action_id is not None and custom_action_id != model.custom_action_id:
                action_result = await session.exec(
                    select(CustomAction).where(
                        (CustomAction.action_id == custom_action_id) &
                        (CustomAction.is_enabled == True)
                    )
                )
                action_model = action_result.first()
                if not action_model:
                    raise ValueError(
                        f"自定义动作 '{custom_action_id}' 不存在或已被禁用。"
                        f"请确保该动作存在且处于启用状态。"
                    )
                
                # 检查权限：如果是私有动作，必须是所有者
                if not action_model.is_public and action_model.mid != model.mid:
                    raise ValueError(
                        f"无权使用私有的自定义动作 '{custom_action_id}'。"
                        f"请联系动作所有者将其公开，或使用您自己的动作。"
                    )
                
                model.custom_action_id = custom_action_id

            # 3. 如果要更新名称，检查是否与同一用户的其他插件重名
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
        """删除插件配置（利用外键级联删除自动清理关联表）
        
        如果删除的是 Fork 版本，会减少原资源的 forks_count
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPlugin).where(UserPlugin.id == id))
            model = result.first()
            if not model:
                return False
            
            # 如果是 Fork 版本，减少原资源的 Fork 计数
            if model.forked_from_id:
                await session.exec(
                    update(UserPlugin)
                    .where(UserPlugin.id == model.forked_from_id)
                    .values(forks_count=UserPlugin.forks_count - 1)
                )
            
            # 由于设置了 foreign_key，数据库会自动处理 workflow_plugin_link 和 action_plugin_link 的清理
            await session.delete(model)
            await session.commit()
            return True

    @staticmethod
    async def enable(id: int) -> bool:
        """启用插件"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(UserPlugin)
                .where(UserPlugin.id == id)
                .values(is_enabled=True, updated_at=datetime.now())
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def disable(id: int) -> bool:
        """禁用插件"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(UserPlugin)
                .where(UserPlugin.id == id)
                .values(is_enabled=False, updated_at=datetime.now())
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def increment_likes(id: int) -> bool:
        """增加点赞数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(UserPlugin)
                .where(UserPlugin.id == id)
                .values(likes_count=UserPlugin.likes_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        """增加举报数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(UserPlugin)
                .where(UserPlugin.id == id)
                .values(reports_count=UserPlugin.reports_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def list_forks(plugin_id: int, skip: int = 0, limit: int = 50) -> List[UserPlugin]:
        """获取某插件的所有 Fork 版本"""
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
    async def fork(id: int, target_mid: str, new_name: str | None = None) -> Optional[UserPlugin]:
        """Fork 插件（仅允许 Fork 公开的插件）
        
        Args:
            id: 原插件ID
            target_mid: 目标用户ID
            new_name: 新名称，如果不提供则使用原名称 + ' (Fork)'
        
        Returns:
            Fork 后的插件模型
            
        Raises:
            ValueError: 如果原插件不是公开的
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPlugin).where(UserPlugin.id == id))
            original = result.first()
            if not original:
                return None
            
            # 只允许 Fork 公开的插件
            if not original.is_public:
                raise ValueError("只能 Fork 公开的插件")

            # 生成新的 plugin_id
            new_plugin_id = f"plugin_{uuid.uuid4().hex[:8]}"
            
            # 如果未提供新名称，使用默认命名
            if not new_name:
                new_name = f"{original.name} (Fork)"
            
            # 检查目标用户下是否已存在同名插件
            existing = await session.exec(
                select(UserPlugin).where(
                    (UserPlugin.mid == target_mid) & (UserPlugin.name == new_name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=new_name, name_type="插件")

            new_model = UserPlugin(
                mid=target_mid,  # 设置为目标用户
                plugin_id=new_plugin_id,
                name=new_name,
                hook_type=original.hook_type,
                custom_action_id=original.custom_action_id,
                description=f"Forked from: {original.name}",
                priority=original.priority,
                is_public=False,  # Fork 后默认为私有
                forked_from_id=original.id,  # 记录来源
            )

            session.add(new_model)
            
            # 增加原资源的 Fork 计数
            await session.exec(
                update(UserPlugin)
                .where(UserPlugin.id == original.id)
                .values(forks_count=UserPlugin.forks_count + 1)
            )
            
            await session.commit()
            await session.refresh(new_model)
            return new_model


class CommunityCrudService:
    """社区互动 CRUD 服务 - 点赞、举报等功能"""

    @staticmethod
    async def toggle_like(
        mid: int,
        resource_type: int,
        resource_id: int,
    ) -> bool | None:
        """
        点赞/取消点赞资源
        
        Args:
            mid: 用户ID
            resource_type: 资源类型 (1=CustomAction, 2=UserWorkflow, 3=UserPlugin)
            resource_id: 资源ID
        
        Returns:
            True: 点赞成功
            False: 取消点赞成功
            None: 资源不存在
        """
        async with DatabaseSessionManager.async_session() as session:
            # 先检查资源是否存在
            if resource_type == ResourceType.CUSTOM_ACTION.value:
                result = await session.exec(
                    select(CustomAction).where(CustomAction.id == resource_id)
                )
                resource = result.first()
            elif resource_type == ResourceType.USER_WORKFLOW.value:
                result = await session.exec(
                    select(UserWorkflow).where(UserWorkflow.id == resource_id)
                )
                resource = result.first()
            elif resource_type == ResourceType.USER_PLUGIN.value:
                result = await session.exec(
                    select(UserPlugin).where(UserPlugin.id == resource_id)
                )
                resource = result.first()
            else:
                return None
            
            if not resource:
                return None
            
            # 检查是否已点赞
            like_result = await session.exec(
                select(ResourceLike).where(
                    (ResourceLike.mid == mid) &
                    (ResourceLike.resource_type == resource_type) &
                    (ResourceLike.resource_id == resource_id)
                )
            )
            existing_like = like_result.first()
            
            if existing_like:
                # 已点赞，取消点赞
                await session.delete(existing_like)
                
                # 减少资源的点赞数
                if resource_type == ResourceType.CUSTOM_ACTION.value:
                    await session.exec(
                        update(CustomAction)
                        .where(CustomAction.id == resource_id)
                        .values(likes_count=CustomAction.likes_count - 1)
                    )
                elif resource_type == ResourceType.USER_WORKFLOW.value:
                    await session.exec(
                        update(UserWorkflow)
                        .where(UserWorkflow.id == resource_id)
                        .values(likes_count=UserWorkflow.likes_count - 1)
                    )
                elif resource_type == ResourceType.USER_PLUGIN.value:
                    await session.exec(
                        update(UserPlugin)
                        .where(UserPlugin.id == resource_id)
                        .values(likes_count=UserPlugin.likes_count - 1)
                    )
                
                await session.commit()
                return False
            else:
                # 未点赞，添加点赞
                like = ResourceLike(
                    mid=mid,
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
                session.add(like)
                
                # 增加资源的点赞数
                if resource_type == ResourceType.CUSTOM_ACTION.value:
                    await session.exec(
                        update(CustomAction)
                        .where(CustomAction.id == resource_id)
                        .values(likes_count=CustomAction.likes_count + 1)
                    )
                elif resource_type == ResourceType.USER_WORKFLOW.value:
                    await session.exec(
                        update(UserWorkflow)
                        .where(UserWorkflow.id == resource_id)
                        .values(likes_count=UserWorkflow.likes_count + 1)
                    )
                elif resource_type == ResourceType.USER_PLUGIN.value:
                    await session.exec(
                        update(UserPlugin)
                        .where(UserPlugin.id == resource_id)
                        .values(likes_count=UserPlugin.likes_count + 1)
                    )
                
                await session.commit()
                return True

    @staticmethod
    async def report(
        mid: int,
        resource_type: int,
        resource_id: int,
        reason: int = 5,  # 默认其他
        description: str = "",
    ) -> bool | None:
        """
        举报资源
        
        Args:
            mid: 用户ID
            resource_type: 资源类型 (1=CustomAction, 2=UserWorkflow, 3=UserPlugin)
            resource_id: 资源ID
            reason: 举报理由 (1=垃圾信息, 2=不当内容, 3=违反规定, 4=抄袭, 5=其他)
            description: 详细描述
        
        Returns:
            True: 举报成功
            False: 已举报过
            None: 资源不存在
        """
        async with DatabaseSessionManager.async_session() as session:
            # 先检查资源是否存在
            if resource_type == ResourceType.CUSTOM_ACTION.value:
                result = await session.exec(
                    select(CustomAction).where(CustomAction.id == resource_id)
                )
                resource = result.first()
            elif resource_type == ResourceType.USER_WORKFLOW.value:
                result = await session.exec(
                    select(UserWorkflow).where(UserWorkflow.id == resource_id)
                )
                resource = result.first()
            elif resource_type == ResourceType.USER_PLUGIN.value:
                result = await session.exec(
                    select(UserPlugin).where(UserPlugin.id == resource_id)
                )
                resource = result.first()
            else:
                return None
            
            if not resource:
                return None
            
            # 检查是否已举报（防刷）
            report_result = await session.exec(
                select(ResourceReport).where(
                    (ResourceReport.mid == mid) &
                    (ResourceReport.resource_type == resource_type) &
                    (ResourceReport.resource_id == resource_id)
                )
            )
            existing_report = report_result.first()
            
            if existing_report:
                # 已举报过
                return False
            
            # 创建举报记录
            report = ResourceReport(
                mid=mid,
                resource_type=resource_type,
                resource_id=resource_id,
                reason=reason,
                description=description,
                is_valid=True,  # 默认有效
            )
            session.add(report)
            
            # 增加资源的举报数
            if resource_type == ResourceType.CUSTOM_ACTION.value:
                await session.exec(
                    update(CustomAction)
                    .where(CustomAction.id == resource_id)
                    .values(reports_count=CustomAction.reports_count + 1)
                )
            elif resource_type == ResourceType.USER_WORKFLOW.value:
                await session.exec(
                    update(UserWorkflow)
                    .where(UserWorkflow.id == resource_id)
                    .values(reports_count=UserWorkflow.reports_count + 1)
                )
            elif resource_type == ResourceType.USER_PLUGIN.value:
                await session.exec(
                    update(UserPlugin)
                    .where(UserPlugin.id == resource_id)
                    .values(reports_count=UserPlugin.reports_count + 1)
                )
            
            await session.commit()
            return True

    @staticmethod
    async def mark_report_invalid(
        report_id: int,
        reviewer_mid: int,
    ) -> bool:
        """
        管理员标记举报为无效
        
        Args:
            report_id: 举报记录ID
            reviewer_mid: 审核管理员ID
        
        Returns:
            True: 操作成功
            False: 举报不存在或已被标记
        """
        async with DatabaseSessionManager.async_session() as session:
            # 获取举报记录
            result = await session.exec(
                select(ResourceReport).where(ResourceReport.id == report_id)
            )
            report = result.first()
            
            if not report or not report.is_valid:
                return False
            
            # 标记为无效
            report.is_valid = False
            report.reviewed_by_mid = reviewer_mid
            report.reviewed_at = datetime.now()
            
            # 减少资源的举报数
            resource_type = report.resource_type
            resource_id = report.resource_id
            
            if resource_type == ResourceType.CUSTOM_ACTION.value:
                await session.exec(
                    update(CustomAction)
                    .where(CustomAction.id == resource_id)
                    .values(reports_count=CustomAction.reports_count - 1)
                )
            elif resource_type == ResourceType.USER_WORKFLOW.value:
                await session.exec(
                    update(UserWorkflow)
                    .where(UserWorkflow.id == resource_id)
                    .values(reports_count=UserWorkflow.reports_count - 1)
                )
            elif resource_type == ResourceType.USER_PLUGIN.value:
                await session.exec(
                    update(UserPlugin)
                    .where(UserPlugin.id == resource_id)
                    .values(reports_count=UserPlugin.reports_count - 1)
                )
            
            await session.commit()
            return True

    @staticmethod
    async def update_report(
        report_id: int,
        mid: int,
        reason: int | None = None,
        description: str | None = None,
    ) -> bool | None:
        """
        修改举报内容（用户只能修改自己的举报）
        
        Args:
            report_id: 举报记录ID
            mid: 用户ID（用于验证是否为自己的举报）
            reason: 新的举报理由（可选）
            description: 新的描述（可选）
        
        Returns:
            True: 修改成功
            False: 权限不足或举报已被处理
            None: 举报记录不存在
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ResourceReport).where(ResourceReport.id == report_id)
            )
            report = result.first()
            
            if not report:
                return None
            
            # 检查权限：只能修改自己的举报
            if report.mid != mid:
                return False
            
            # 检查状态：已被标记为无效的举报不能修改
            if not report.is_valid:
                return False
            
            # 更新字段
            if reason is not None:
                report.reason = reason
            if description is not None:
                report.description = description
            
            await session.commit()
            return True

    @staticmethod
    async def list_reports(
        skip: int = 0,
        limit: int = 50,
        is_valid: bool | None = None,
        resource_type: int | None = None,
    ) -> List[ResourceReport]:
        """
        获取举报列表（管理员用）
        
        Args:
            skip: 跳过记录数
            limit: 返回记录数
            is_valid: 是否有效（None=全部, True=有效, False=无效）
            resource_type: 资源类型筛选
        
        Returns:
            举报记录列表
        """
        async with DatabaseSessionManager.async_session() as session:
            query = select(ResourceReport)
            
            if is_valid is not None:
                query = query.where(ResourceReport.is_valid == is_valid)
            
            if resource_type is not None:
                query = query.where(ResourceReport.resource_type == resource_type)
            
            query = query.order_by(ResourceReport.created_at.desc())
            query = query.offset(skip).limit(limit)
            
            result = await session.exec(query)
            return result.all()

    @staticmethod
    async def count_reports(
        is_valid: bool | None = None,
        resource_type: int | None = None,
    ) -> int:
        """
        获取举报总数（管理员用）
        
        Args:
            is_valid: 是否有效（None=全部, True=有效, False=无效）
            resource_type: 资源类型筛选
        
        Returns:
            举报记录总数
        """
        async with DatabaseSessionManager.async_session() as session:
            query = select(ResourceReport)
            
            if is_valid is not None:
                query = query.where(ResourceReport.is_valid == is_valid)
            
            if resource_type is not None:
                query = query.where(ResourceReport.resource_type == resource_type)
            
            result = await session.exec(query)
            return len(result.all())


# 全局服务实例
action_crud = ActionCrudService()
workflow_crud = WorkflowCrudService()
plugin_crud = PluginCrudService()
execution_log_crud = ExecutionLogCrudService()
community_crud = CommunityCrudService()
