"""
执行模块 CRUD 服务

提供操作、插件、工作流的完整 CRUD 功能
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import uuid
from sqlmodel import select, update

from app.models.database.workflow.models import (
    CustomActionModel,
    UserPluginModel,
    UserWorkflowModel,
    WorkflowExecutionLogModel,
    WorkflowPluginLink,
    ActionPluginLink,
    ActionType,
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
    ) -> CustomActionModel:
        """创建自定义操作（仅支持预定义动作组合）
        
        Raises:
            ValueError: 如果同一用户下已存在同名操作
        """
        async with DatabaseSessionManager.async_session() as session:
            # 检查同一用户下是否已存在同名操作
            existing = await session.exec(
                select(CustomActionModel).where(
                    (CustomActionModel.mid == mid) & (CustomActionModel.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="操作")
            
            model = CustomActionModel(
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
                    link = ActionPluginLink(
                        action_id=action_id,
                        plugin_id=link_data.get("plugin_id"),
                        config_params=link_data.get("config_params", {})
                    )
                    session.add(link)
            
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
    async def get_enabled_plugins(action_id: str) -> List[Dict[str, Any]]:
        """获取操作关联的插件列表"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ActionPluginLink).where(ActionPluginLink.action_id == action_id)
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
            query = select(func.count(CustomActionModel.id))
            
            # 应用筛选条件
            if filter_type == "private":
                query = query.where(
                    (CustomActionModel.mid == mid) & (CustomActionModel.is_public == False)
                )
            elif filter_type == "public":
                query = query.where(
                    (CustomActionModel.mid == mid) & (CustomActionModel.is_public == True)
                )
            elif filter_type == "community":
                query = query.where(
                    (CustomActionModel.mid != mid) & (CustomActionModel.is_public == True)
                )
            elif filter_type == "verified":
                query = query.where(CustomActionModel.is_verified == True)
            else:
                query = query.where(
                    (CustomActionModel.mid == mid) | (CustomActionModel.is_public == True)
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
    ) -> List[CustomActionModel]:
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
            query = select(CustomActionModel)
            
            # 应用筛选条件
            if filter_type == "private":
                # 我的私有：当前用户的且未公开
                query = query.where(
                    (CustomActionModel.mid == mid) & (CustomActionModel.is_public == False)
                )
            elif filter_type == "public":
                # 我的公开：当前用户的且已公开
                query = query.where(
                    (CustomActionModel.mid == mid) & (CustomActionModel.is_public == True)
                )
            elif filter_type == "community":
                # 社区公开：非当前用户的且已公开
                query = query.where(
                    (CustomActionModel.mid != mid) & (CustomActionModel.is_public == True)
                )
            elif filter_type == "verified":
                # 已认证：所有已认证的
                query = query.where(CustomActionModel.is_verified == True)
            else:
                # all: 查询属于该用户的 OR 公开给所有人的
                query = query.where(
                    (CustomActionModel.mid == mid) | (CustomActionModel.is_public == True)
                )
            
            # 应用排序
            sort_field = getattr(CustomActionModel, sort_by, CustomActionModel.updated_at)
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
    ) -> Optional[CustomActionModel]:
        """更新操作（仅支持预定义动作组合）
        
        Raises:
            ValueError: 如果同一用户下已存在同名操作
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(CustomActionModel).where(CustomActionModel.id == id))
            model = result.first()
            if not model:
                return None

            # 如果要更新名称，检查是否与同一用户的其他操作重名
            if name is not None and name != model.name:
                existing = await session.exec(
                    select(CustomActionModel).where(
                        (CustomActionModel.mid == model.mid) & 
                        (CustomActionModel.name == name) &
                        (CustomActionModel.id != id)
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
                    select(ActionPluginLink).where(ActionPluginLink.action_id == model.action_id)
                )
                for link in old_links.all():
                    await session.delete(link)
                
                for link_data in enabled_plugins:
                    link = ActionPluginLink(
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
            result = await session.exec(select(CustomActionModel).where(CustomActionModel.id == id))
            model = result.first()
            if not model:
                return False
            
            # 如果是 Fork 版本，减少原资源的 Fork 计数
            if model.forked_from_id:
                await session.exec(
                    update(CustomActionModel)
                    .where(CustomActionModel.id == model.forked_from_id)
                    .values(forks_count=CustomActionModel.forks_count - 1)
                )
            
            # 先删除关联的插件记录
            links = await session.exec(
                select(ActionPluginLink).where(ActionPluginLink.action_id == model.action_id)
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

    @staticmethod
    async def increment_likes(id: int) -> bool:
        """增加点赞数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(CustomActionModel)
                .where(CustomActionModel.id == id)
                .values(likes_count=CustomActionModel.likes_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        """增加举报数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(CustomActionModel)
                .where(CustomActionModel.id == id)
                .values(reports_count=CustomActionModel.reports_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def list_forks(action_id: int, skip: int = 0, limit: int = 50) -> List[CustomActionModel]:
        """获取某操作的所有 Fork 版本"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CustomActionModel)
                .where(CustomActionModel.forked_from_id == action_id)
                .order_by(CustomActionModel.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def fork(id: int, target_mid: str, new_name: str | None = None) -> Optional[CustomActionModel]:
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
            result = await session.exec(select(CustomActionModel).where(CustomActionModel.id == id))
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
                select(CustomActionModel).where(
                    (CustomActionModel.mid == target_mid) & (CustomActionModel.name == new_name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=new_name, name_type="操作")

            new_model = CustomActionModel(
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
                update(CustomActionModel)
                .where(CustomActionModel.id == original.id)
                .values(forks_count=CustomActionModel.forks_count + 1)
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
    ) -> UserWorkflowModel:
        """创建工作流
        
        Raises:
            ValueError: 如果同一用户下已存在同名工作流
        """
        async with DatabaseSessionManager.async_session() as session:
            # 检查同一用户下是否已存在同名工作流
            existing = await session.exec(
                select(UserWorkflowModel).where(
                    (UserWorkflowModel.mid == mid) & (UserWorkflowModel.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="工作流")
            
            model = UserWorkflowModel(
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
                    link = WorkflowPluginLink(
                        workflow_id=workflow_id,
                        plugin_id=link_data.get("plugin_id"),
                        config_params=link_data.get("config_params", {})
                    )
                    session.add(link)
            
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
    async def get_enabled_plugins(workflow_id: str) -> List[Dict[str, Any]]:
        """获取工作流关联的插件列表
        
        Returns:
            List[Dict]: 包含 plugin_id 和 config_params 的列表
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowPluginLink).where(WorkflowPluginLink.workflow_id == workflow_id)
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
            query = select(func.count(UserWorkflowModel.id))
            
            # 应用筛选条件
            if filter_type == "private":
                query = query.where(
                    (UserWorkflowModel.mid == mid) & (UserWorkflowModel.is_public == False)
                )
            elif filter_type == "public":
                query = query.where(
                    (UserWorkflowModel.mid == mid) & (UserWorkflowModel.is_public == True)
                )
            elif filter_type == "community":
                query = query.where(
                    (UserWorkflowModel.mid != mid) & (UserWorkflowModel.is_public == True)
                )
            elif filter_type == "verified":
                query = query.where(UserWorkflowModel.is_verified == True)
            else:
                query = query.where(
                    (UserWorkflowModel.mid == mid) | (UserWorkflowModel.is_public == True)
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
    ) -> List[UserWorkflowModel]:
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
            query = select(UserWorkflowModel)
            
            # 应用筛选条件
            if filter_type == "private":
                # 我的私有：当前用户的且未公开
                query = query.where(
                    (UserWorkflowModel.mid == mid) & (UserWorkflowModel.is_public == False)
                )
            elif filter_type == "public":
                # 我的公开：当前用户的且已公开
                query = query.where(
                    (UserWorkflowModel.mid == mid) & (UserWorkflowModel.is_public == True)
                )
            elif filter_type == "community":
                # 社区公开：非当前用户的且已公开
                query = query.where(
                    (UserWorkflowModel.mid != mid) & (UserWorkflowModel.is_public == True)
                )
            elif filter_type == "verified":
                # 已认证：所有已认证的
                query = query.where(UserWorkflowModel.is_verified == True)
            else:
                # all: 根据include_public参数决定
                if include_public:
                    query = query.where((UserWorkflowModel.mid == mid) | (UserWorkflowModel.is_public == True))
                else:
                    query = query.where(UserWorkflowModel.mid == mid)
            
            # 应用排序
            sort_field = getattr(UserWorkflowModel, sort_by, UserWorkflowModel.updated_at)
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
    ) -> Optional[UserWorkflowModel]:
        """更新工作流
        
        Raises:
            ValueError: 如果同一用户下已存在同名工作流
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserWorkflowModel).where(UserWorkflowModel.id == id))
            model = result.first()
            if not model:
                return None

            # 如果要更新名称，检查是否与同一用户的其他工作流重名
            if name is not None and name != model.name:
                existing = await session.exec(
                    select(UserWorkflowModel).where(
                        (UserWorkflowModel.mid == model.mid) & 
                        (UserWorkflowModel.name == name) &
                        (UserWorkflowModel.id != id)
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
                    select(WorkflowPluginLink).where(WorkflowPluginLink.workflow_id == model.workflow_id)
                )
                for link in old_links.all():
                    await session.delete(link)
                
                # 增加新的关联
                for link_data in enabled_plugins:
                    link = WorkflowPluginLink(
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
            result = await session.exec(select(UserWorkflowModel).where(UserWorkflowModel.id == id))
            model = result.first()
            if not model:
                return False
            
            # 如果是 Fork 版本，减少原资源的 Fork 计数
            if model.forked_from_id:
                await session.exec(
                    update(UserWorkflowModel)
                    .where(UserWorkflowModel.id == model.forked_from_id)
                    .values(forks_count=UserWorkflowModel.forks_count - 1)
                )
            
            # 先删除关联的插件记录
            links = await session.exec(
                select(WorkflowPluginLink).where(WorkflowPluginLink.workflow_id == model.workflow_id)
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
        """复制工作流（同一用户）"""
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
    async def fork(id: int, target_mid: str, new_name: str | None = None) -> Optional[UserWorkflowModel]:
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
            result = await session.exec(select(UserWorkflowModel).where(UserWorkflowModel.id == id))
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
                select(UserWorkflowModel).where(
                    (UserWorkflowModel.mid == target_mid) & (UserWorkflowModel.name == new_name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=new_name, name_type="工作流")

            new_model = UserWorkflowModel(
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
                update(UserWorkflowModel)
                .where(UserWorkflowModel.id == original.id)
                .values(forks_count=UserWorkflowModel.forks_count + 1)
            )
            
            await session.commit()
            await session.refresh(new_model)
            return new_model

    @staticmethod
    async def increment_likes(id: int) -> bool:
        """增加点赞数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(UserWorkflowModel)
                .where(UserWorkflowModel.id == id)
                .values(likes_count=UserWorkflowModel.likes_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        """增加举报数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(UserWorkflowModel)
                .where(UserWorkflowModel.id == id)
                .values(reports_count=UserWorkflowModel.reports_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def list_forks(workflow_id: int, skip: int = 0, limit: int = 50) -> List[UserWorkflowModel]:
        """获取某工作流的所有 Fork 版本"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserWorkflowModel)
                .where(UserWorkflowModel.forked_from_id == workflow_id)
                .order_by(UserWorkflowModel.created_at.desc())
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
                select(WorkflowExecutionLogModel)
                .where(WorkflowExecutionLogModel.execution_id == execution_id)
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
    ) -> UserPluginModel:
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
                select(CustomActionModel).where(
                    (CustomActionModel.action_id == custom_action_id) &
                    (CustomActionModel.is_enabled == True)
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
                select(UserPluginModel).where(
                    (UserPluginModel.mid == mid) & (UserPluginModel.name == name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=name, name_type="插件")
            
            # 5. 创建插件配置
            model = UserPluginModel(
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
    async def get_by_id(id: int) -> Optional[UserPluginModel]:
        """根据ID获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPluginModel).where(UserPluginModel.id == id))
            return result.first()

    @staticmethod
    async def get_by_plugin_id(plugin_id: str) -> Optional[UserPluginModel]:
        """根据 plugin_id 获取"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserPluginModel).where(UserPluginModel.plugin_id == plugin_id)
            )
            return result.first()

    @staticmethod
    async def get_by_ids(ids: List[str]) -> List[UserPluginModel]:
        """根据ID列表批量获取插件详情"""
        if not ids:
            return []
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserPluginModel)
                .where(UserPluginModel.plugin_id.in_(ids))
                .where(UserPluginModel.is_enabled == True)
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
            query = select(func.count(UserPluginModel.id))
            
            # 应用筛选条件
            if filter_type == "private":
                query = query.where(
                    (UserPluginModel.mid == mid) & (UserPluginModel.is_public == False)
                )
            elif filter_type == "public":
                query = query.where(
                    (UserPluginModel.mid == mid) & (UserPluginModel.is_public == True)
                )
            elif filter_type == "community":
                query = query.where(
                    (UserPluginModel.mid != mid) & (UserPluginModel.is_public == True)
                )
            elif filter_type == "verified":
                query = query.where(UserPluginModel.is_verified == True)
            else:
                query = query.where(
                    (UserPluginModel.mid == mid) | (UserPluginModel.is_public == True)
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
    ) -> List[UserPluginModel]:
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
            query = select(UserPluginModel)
            
            # 应用筛选条件
            if filter_type == "private":
                # 我的私有：当前用户的且未公开
                query = query.where(
                    (UserPluginModel.mid == mid) & (UserPluginModel.is_public == False)
                )
            elif filter_type == "public":
                # 我的公开：当前用户的且已公开
                query = query.where(
                    (UserPluginModel.mid == mid) & (UserPluginModel.is_public == True)
                )
            elif filter_type == "community":
                # 社区公开：非当前用户的且已公开
                query = query.where(
                    (UserPluginModel.mid != mid) & (UserPluginModel.is_public == True)
                )
            elif filter_type == "verified":
                # 已认证：所有已认证的
                query = query.where(UserPluginModel.is_verified == True)
            else:
                # all: 查询属于该用户的 OR 公开给所有人的
                query = query.where(
                    (UserPluginModel.mid == mid) | (UserPluginModel.is_public == True)
                )
            
            # 应用排序
            sort_field = getattr(UserPluginModel, sort_by, UserPluginModel.updated_at)
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
    ) -> Optional[UserPluginModel]:
        """更新插件配置
        
        Raises:
            ValueError: 如果同一用户下已存在同名插件
            ValueError: 如果钩子类型无效
            ValueError: 如果自定义动作不存在或不可用
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(UserPluginModel).where(UserPluginModel.id == id))
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
                    select(CustomActionModel).where(
                        (CustomActionModel.action_id == custom_action_id) &
                        (CustomActionModel.is_enabled == True)
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
                    select(UserPluginModel).where(
                        (UserPluginModel.mid == model.mid) & 
                        (UserPluginModel.name == name) &
                        (UserPluginModel.id != id)
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
            result = await session.exec(select(UserPluginModel).where(UserPluginModel.id == id))
            model = result.first()
            if not model:
                return False
            
            # 如果是 Fork 版本，减少原资源的 Fork 计数
            if model.forked_from_id:
                await session.exec(
                    update(UserPluginModel)
                    .where(UserPluginModel.id == model.forked_from_id)
                    .values(forks_count=UserPluginModel.forks_count - 1)
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
                update(UserPluginModel)
                .where(UserPluginModel.id == id)
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
                update(UserPluginModel)
                .where(UserPluginModel.id == id)
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
                update(UserPluginModel)
                .where(UserPluginModel.id == id)
                .values(likes_count=UserPluginModel.likes_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def increment_reports(id: int) -> bool:
        """增加举报数"""
        async with DatabaseSessionManager.async_session() as session:
            stmt = (
                update(UserPluginModel)
                .where(UserPluginModel.id == id)
                .values(reports_count=UserPluginModel.reports_count + 1)
            )
            await session.exec(stmt)
            await session.commit()
            return True

    @staticmethod
    async def list_forks(plugin_id: int, skip: int = 0, limit: int = 50) -> List[UserPluginModel]:
        """获取某插件的所有 Fork 版本"""
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(UserPluginModel)
                .where(UserPluginModel.forked_from_id == plugin_id)
                .order_by(UserPluginModel.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return result.all()

    @staticmethod
    async def fork(id: int, target_mid: str, new_name: str | None = None) -> Optional[UserPluginModel]:
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
            result = await session.exec(select(UserPluginModel).where(UserPluginModel.id == id))
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
                select(UserPluginModel).where(
                    (UserPluginModel.mid == target_mid) & (UserPluginModel.name == new_name)
                )
            )
            if existing.first():
                raise NameAlreadyExistsException(name=new_name, name_type="插件")

            new_model = UserPluginModel(
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
                update(UserPluginModel)
                .where(UserPluginModel.id == original.id)
                .values(forks_count=UserPluginModel.forks_count + 1)
            )
            
            await session.commit()
            await session.refresh(new_model)
            return new_model


# 全局服务实例
action_crud = ActionCrudService()
workflow_crud = WorkflowCrudService()
plugin_crud = PluginCrudService()
execution_log_crud = ExecutionLogCrudService()
