"""
插件挂载管理路由

负责管理 UserPlugin，即定义在哪些生命周期钩子处自动执行哪些 CustomAction。
"""
from fastapi import Depends, APIRouter
from sqlmodel import select
from typing import List
import uuid

from app.models.response import StandardResponse, success_response, error_response
from app.models.workflow.models import (
    PluginCreateRequest,
    PluginUpdateRequest,
    PluginDetailResponse,
    PluginListItemResponse,
    PluginListRequest,
    PluginForkRequest,
    PluginForkResponse,
)
from app.models.exceptions.base_exception import NameAlreadyExistsException
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.services.execution.crud_service import plugin_crud, action_crud, workflow_crud
from app.models.base.base_sqlmodel import BasePaginationResp
from ..base import new_plugin_router

router = new_plugin_router()


@router.post("/plugins/list", response_model=StandardResponse[BasePaginationResp[PluginListItemResponse]])
async def list_plugins(
    request: PluginListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """获取用户的插件挂载配置列表"""
    # 计算 skip
    skip = (request.page - 1) * request.per_page
    
    # 获取总数
    total = await plugin_crud.count_by_user(
        mid=auth.mid,
        filter_type=request.filter_type
    )
    
    # 获取列表数据
    models = await plugin_crud.list_by_user(
        mid=auth.mid,
        skip=skip,
        limit=request.per_page,
        filter_type=request.filter_type,
        sort_by=request.sort_by,
        sort_order=request.sort_order
    )
    
    items = [
        PluginListItemResponse(
            id=m.id,
            plugin_id=m.plugin_id,
            name=m.name,
            hook_type=m.hook_type,
            custom_action_id=m.custom_action_id,
            is_enabled=m.is_enabled,
            priority=m.priority,
            is_public=m.is_public,
            likes_count=m.likes_count,
            reports_count=m.reports_count,
            is_verified=m.is_verified,
            forks_count=m.forks_count,
            forked_from_id=m.forked_from_id,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in models
    ]
    
    # 构建分页响应
    pagination = BasePaginationResp[PluginListItemResponse](
        page=request.page,
        per_page=request.per_page,
        total=total,
        items=items
    )
    
    return success_response(pagination)


@router.post("/plugins/create", response_model=StandardResponse[PluginDetailResponse])
async def create_plugin(
    request: PluginCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """创建插件挂载配置"""
    try:
        plugin_id = f"plugin_{uuid.uuid4().hex[:8]}"
        
        model = await plugin_crud.create(
            mid=auth.mid,
            plugin_id=plugin_id,
            name=request.name,
            hook_type=request.hook_type,
            custom_action_id=request.custom_action_id,
            description=request.description,
            priority=request.priority,
            is_public=request.is_public,
        )
        
        return success_response(PluginDetailResponse(
            id=model.id,
            plugin_id=model.plugin_id,
            name=model.name,
            hook_type=model.hook_type,
            custom_action_id=model.custom_action_id,
            description=model.description,
            is_enabled=model.is_enabled,
            priority=model.priority,
            is_public=model.is_public,
        ))
    except ValueError as e:
        # 捕获验证错误并返回友好的错误信息
        return error_response(str(e))
    except NameAlreadyExistsException as e:
        # 捕获名称重复错误
        return error_response(str(e))


@router.post("/plugins/update", response_model=StandardResponse[str])
async def update_plugin(
    request: PluginUpdateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """更新插件挂载配置"""
    model = await plugin_crud.get_by_id(request.id)
    if not model or model.mid != auth.mid:
        return error_response("插件配置不存在")

    try:
        await plugin_crud.update(
            id=request.id,
            name=request.name,
            description=request.description,
            hook_type=request.hook_type,
            custom_action_id=request.custom_action_id,
            priority=request.priority,
            is_public=request.is_public,
        )
    except ValueError as e:
        # 捕获验证错误并返回友好的错误信息
        return error_response(str(e))
    except NameAlreadyExistsException as e:
        # 捕获名称重复错误
        return error_response(str(e))
    
    if request.is_enabled is not None:
        if request.is_enabled:
            await plugin_crud.enable(request.id)
        else:
            await plugin_crud.disable(request.id)

    return success_response("更新成功")


@router.post("/plugins/delete", response_model=StandardResponse[str])
async def delete_plugin(
    id: int,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """删除插件挂载配置"""
    model = await plugin_crud.get_by_id(id)
    if not model or model.mid != auth.mid:
        return error_response("插件配置不存在")

    await plugin_crud.delete(id)
    return success_response("删除成功")


@router.post("/plugins/fork", summary="Fork 插件（类似 GitHub）", response_model=StandardResponse[PluginForkResponse])
async def fork_plugin(
    request: PluginForkRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[PluginForkResponse]:
    """Fork 插件
    
    - 如果是自己的插件：允许无条件 Fork（类似“创建副本”）
    - 如果是别人的插件：仅允许 Fork 公开的插件（类似 GitHub）
    
    Args:
        request: {"id": <插件ID>, "new_name": <新名称（可选）>}
    """
    # 获取原插件
    original = await plugin_crud.get_by_id(request.id)
    if not original:
        return error_response(404, "插件不存在")
    
    # 检查权限：如果是别人的插件，必须是公开的
    if original.mid != auth.mid and not original.is_public:
        return error_response(403, "只能 Fork 公开的插件或自己的插件")
    
    try:
        # 执行 Fork
        model = await plugin_crud.fork(
            id=request.id,
            target_mid=auth.mid,
            new_name=request.new_name
        )
        
        if not model:
            return error_response(500, "Fork 失败")
        
        return success_response(
            PluginForkResponse(
                id=model.id,
                plugin_id=model.plugin_id,
                name=model.name,
                forked_from=original.name,
            ),
            message="Fork 成功"
        )
    except ValueError as e:
        return error_response(400, str(e))


@router.get("/plugins/{id}/forks", summary="获取插件的所有 Fork 版本")
async def get_plugin_forks(
    id: int,
    skip: int = 0,
    limit: int = 50,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[PluginListItemResponse]]:
    """获取某插件的所有 Fork 版本列表"""
    original = await plugin_crud.get_by_id(id)
    if not original:
        return error_response(404, "插件不存在")
    
    forks = await plugin_crud.list_forks(id, skip, limit)
    
    items = [
        PluginListItemResponse(
            id=f.id,
            plugin_id=f.plugin_id,
            name=f.name,
            hook_type=f.hook_type,
            custom_action_id=f.custom_action_id,
            is_enabled=f.is_enabled,
            priority=f.priority,
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
    
    pagination = BasePaginationResp[PluginListItemResponse](
        page=1,
        per_page=limit,
        total=len(items),
        items=items
    )
    
    return success_response(pagination)
