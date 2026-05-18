"""
Action 管理路由

提供系统预注册 Action 和用户自定义 Action（Custom Action）的 API
"""
from loguru import logger
from typing import Any, List
import uuid
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from fastapi import APIRouter, Depends
from app.services.execution.crud_service import action_crud
from app.services.execution.action_registry import action_registry
from app.models.workflow.models import (
    CustomActionCreateRequest,
    CustomActionUpdateRequest,
    CustomActionListRequest,
    CustomActionDetailResponse,
    CustomActionListItemResponse,
    ActionForkRequest,
    ActionForkResponse,
)
from app.models.database.workflow.models import ActionMetadataResponse
from app.models.base.base_sqlmodel import BasePaginationResp
from ..base import new_action_router

router = new_action_router()


# ============ 系统预注册操作（只读，全 POST） ============


@router.post(BrowserControlRouterPath.actions_registered, summary="获取系统预注册操作列表")
async def list_registered_actions() -> StandardResponse[List[ActionMetadataResponse]]:
    """获取系统预注册操作列表（公开，只读）
    
    返回精简版 Action 元数据，仅包含 action_id 和 json_schema
    """
    actions = action_registry.get_all_actions()
    # 转换为精简版响应
    response_actions = [
        ActionMetadataResponse(
            action_id=action.id,
            json_schema=action.json_schema or {},
        )
        for action in actions
    ]
    return success_response(response_actions)


# ============ 自定义操作管理（用户自定义 Action） ============


@router.post(BrowserControlRouterPath.custom_actions_create, summary="创建自定义操作")
async def create_custom_action(
    request: CustomActionCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CustomActionDetailResponse]:
    """创建用户自定义操作
    
    用户可以基于系统预注册的操作或插件组合创建自己的操作。
    action_id 由系统自动生成（格式：ca_xxx），用户仅需提供显示名称 name。
    """
    # 生成唯一的 action_id（格式：ca_xxx）
    action_id = f"ca_{uuid.uuid4().hex[:12]}"
    
    model = await action_crud.create(
        action_id=action_id,
        name=request.name,
        mid=auth.mid,
        description=request.description,
        steps=request.steps,
        enabled_plugins=request.enabled_plugins or [],
        tags=request.tags or [],
    )
    
    # 获取关联的插件列表
    enabled_plugins = await action_crud.get_enabled_plugins(model.action_id)
    enabled_plugin_ids = [p["plugin_id"] for p in enabled_plugins]
    
    return success_response(
        CustomActionDetailResponse(
            id=model.id,
            action_id=model.action_id,
            name=model.name,
            description=model.description,
            steps=model.steps,
            enabled_plugins=enabled_plugin_ids,
            tags=model.tags,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.custom_actions_list, summary="获取自定义操作列表")
async def list_custom_actions(
    request: CustomActionListRequest = None,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[CustomActionListItemResponse]]:
    """获取当前用户的自定义操作列表
    
    支持分页、筛选和排序
    """
    if request is None:
        request = CustomActionListRequest()
    
    # 计算 skip
    skip = (request.page - 1) * request.per_page
    
    # 获取总数
    total = await action_crud.count_by_user(
        mid=auth.mid,
        filter_type=request.filter_type
    )
    
    # 获取列表数据
    models = await action_crud.list_by_user(
        mid=auth.mid,
        skip=skip,
        limit=request.per_page,
        filter_type=request.filter_type,
        sort_by=request.sort_by,
        sort_order=request.sort_order
    )
    
    items = []
    for model in models:
        items.append(
            CustomActionListItemResponse(
                id=model.id,
                action_id=model.action_id,
                name=model.name,
                action_type=model.action_type,
                description=model.description,
                steps_count=len(model.steps) if model.steps else 0,
                is_enabled=model.is_enabled,
                is_public=model.is_public,
                likes_count=model.likes_count,
                reports_count=model.reports_count,
                is_verified=model.is_verified,
                forks_count=model.forks_count,
                forked_from_id=model.forked_from_id,
                created_at=model.created_at,
                updated_at=model.updated_at,
            )
        )
    
    # 构建分页响应
    pagination = BasePaginationResp[CustomActionListItemResponse](
        page=request.page,
        per_page=request.per_page,
        total=total,
        items=items
    )
    
    return success_response(pagination)


@router.post(BrowserControlRouterPath.custom_actions_get, summary="获取自定义操作详情")
async def get_custom_action(
    request: dict,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CustomActionDetailResponse]:
    """获取自定义操作详情
    
    Args:
        request: {"id": <操作ID>}
    """
    action_id = request.get("id")
    if not action_id:
        return error_response(400, "缺少操作ID")
    
    model = await action_crud.get_by_id(action_id)
    if not model or model.mid != auth.mid:
        return error_response(404, "操作不存在")
    
    # 获取关联的插件列表
    enabled_plugins = await action_crud.get_enabled_plugins(model.action_id)
    enabled_plugin_ids = [p["plugin_id"] for p in enabled_plugins]
    
    return success_response(
        CustomActionDetailResponse(
            id=model.id,
            action_id=model.action_id,
            name=model.name,
            version=model.version,
            action_type=model.action_type,
            description=model.description,
            parameters_schema=model.parameters_schema,
            steps=model.steps,
            enabled_plugins=enabled_plugin_ids,
            tags=model.tags,
            user_data=model.user_data,
            is_enabled=model.is_enabled,
            is_public=model.is_public,
            timeout=model.timeout,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.custom_actions_update, summary="更新自定义操作")
async def update_custom_action(
    request: CustomActionUpdateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CustomActionDetailResponse]:
    """更新自定义操作"""
    model = await action_crud.update(
        id=request.id,
        name=request.name,
        description=request.description,
        steps=request.steps,
        enabled_plugins=request.enabled_plugins,
        tags=request.tags,
    )
    
    if not model or model.mid != auth.mid:
        return error_response(404, "操作不存在或无权限")
    
    # 获取关联的插件列表
    enabled_plugins = await action_crud.get_enabled_plugins(model.action_id)
    enabled_plugin_ids = [p["plugin_id"] for p in enabled_plugins]
    
    return success_response(
        CustomActionDetailResponse(
            id=model.id,
            action_id=model.action_id,
            name=model.name,
            version=model.version,
            action_type=model.action_type,
            description=model.description,
            parameters_schema=model.parameters_schema,
            steps=model.steps,
            enabled_plugins=enabled_plugin_ids,
            tags=model.tags,
            user_data=model.user_data,
            is_enabled=model.is_enabled,
            is_public=model.is_public,
            timeout=model.timeout,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.custom_actions_delete, summary="删除自定义操作")
async def delete_custom_action(
    request: dict,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """删除自定义操作
    
    Args:
        request: {"id": <操作ID>}
    """
    action_id = request.get("id")
    if not action_id:
        return error_response(400, "缺少操作ID")
    
    model = await action_crud.get_by_id(action_id)
    if not model or model.mid != auth.mid:
        return error_response(404, "操作不存在或无权限")
    
    success = await action_crud.delete(action_id)
    if success:
        return success_response({"message": "删除成功"})
    else:
        return error_response(500, "删除失败")


@router.post("/custom_actions/fork", summary="Fork 自定义操作（类似 GitHub）", response_model=StandardResponse[ActionForkResponse])
async def fork_custom_action(
    request: ActionForkRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[ActionForkResponse]:
    """Fork 自定义操作（仅允许 Fork 公开的操作）
    
    类似 GitHub 的 Fork 功能，允许用户复制公开的社区操作到自己的空间，并可以选择重命名。
    
    Args:
        request: {"id": <操作ID>, "new_name": <新名称（可选）>}
    """
    # 获取原操作
    original = await action_crud.get_by_id(request.id)
    if not original:
        return error_response(404, "操作不存在")
    
    # 检查是否为公开操作
    if not original.is_public:
        return error_response(403, "只能 Fork 公开的操作")
    
    try:
        # 执行 Fork
        model = await action_crud.fork(
            id=request.id,
            target_mid=auth.mid,
            new_name=request.new_name
        )
        
        if not model:
            return error_response(500, "Fork 失败")
        
        return success_response(
            ActionForkResponse(
                id=model.id,
                action_id=model.action_id,
                name=model.name,
                forked_from=original.name,
            ),
            message="Fork 成功"
        )
    except ValueError as e:
        return error_response(400, str(e))


@router.get("/custom_actions/{id}/forks", summary="获取自定义操作的所有 Fork 版本")
async def get_action_forks(
    id: int,
    skip: int = 0,
    limit: int = 50,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[CustomActionListItemResponse]]:
    """获取某自定义操作的所有 Fork 版本列表"""
    original = await action_crud.get_by_id(id)
    if not original:
        return error_response(404, "操作不存在")
    
    forks = await action_crud.list_forks(id, skip, limit)
    
    items = [
        CustomActionListItemResponse(
            id=f.id,
            action_id=f.action_id,
            name=f.name,
            action_type=f.action_type,
            description=f.description,
            steps_count=len(f.steps) if f.steps else 0,
            is_enabled=f.is_enabled,
            is_public=f.is_public,
            likes_count=f.likes_count,
            reports_count=f.reports_count,
            is_verified=f.is_verified,
            forks_count=f.forks_count,
            forked_from_id=f.forked_from_id,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in forks
    ]
    
    return success_response(items)
