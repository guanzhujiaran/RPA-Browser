"""
社区互动路由 - 提供社区公开资源浏览、点赞、举报等功能
"""
from loguru import logger
from typing import Any, List
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from fastapi import APIRouter, Depends
from app.services.execution.crud_service import action_crud, workflow_crud, plugin_crud, community_crud
from app.models.workflow.models import (
    CustomActionListItemResponse,
    WorkflowListItemResponse,
    PluginListItemResponse,
    FilterType,
    SortBy,
    SortOrder,
    ActionForkRequest,
    ActionForkResponse,
    WorkflowForkRequest,
    WorkflowForkResponse,
    PluginForkRequest,
    PluginForkResponse,
)
from app.models.base.base_sqlmodel import BasePaginationReq, BasePaginationResp
from ..base import new_community_router

router = new_community_router()


class CommunityListRequest(BasePaginationReq):
    """社区列表请求"""
    filter_type: FilterType = FilterType.COMMUNITY
    sort_by: SortBy = SortBy.LIKES_COUNT
    sort_order: SortOrder = SortOrder.DESC


# ============ 社区公开列表 API ============


@router.post("/community/actions/list", summary="获取社区公开操作列表")
async def list_community_actions(
    request: CommunityListRequest = None,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[CustomActionListItemResponse]]:
    """获取社区公开的自定义操作列表（非当前用户的公开操作）"""
    if request is None:
        request = CommunityListRequest()
    
    skip = (request.page - 1) * request.per_page
    
    # 获取总数
    total = await action_crud.count_by_user(
        mid=auth.mid,
        filter_type=request.filter_type
    )
    
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
    
    pagination = BasePaginationResp[CustomActionListItemResponse](
        page=request.page,
        per_page=request.per_page,
        total=total,
        items=items
    )
    
    return success_response(pagination)


@router.post("/community/workflows/list", summary="获取社区公开工作流列表")
async def list_community_workflows(
    request: CommunityListRequest = None,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[WorkflowListItemResponse]]:
    """获取社区公开的工作流列表（非当前用户的公开工作流）"""
    if request is None:
        request = CommunityListRequest()
    
    skip = (request.page - 1) * request.per_page
    
    # 获取总数
    total = await workflow_crud.count_by_user(
        mid=auth.mid,
        filter_type=request.filter_type
    )
    
    models = await workflow_crud.list_by_user(
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
            WorkflowListItemResponse(
                id=model.id,
                workflow_id=model.workflow_id,
                name=model.name,
                description=model.description,
                tags=model.tags,
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
    
    pagination = BasePaginationResp[WorkflowListItemResponse](
        page=request.page,
        per_page=request.per_page,
        total=total,
        items=items
    )
    
    return success_response(pagination)


@router.post("/community/plugins/list", summary="获取社区公开插件列表")
async def list_community_plugins(
    request: CommunityListRequest = None,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[PluginListItemResponse]]:
    """获取社区公开的插件列表（非当前用户的公开插件）"""
    if request is None:
        request = CommunityListRequest()
    
    skip = (request.page - 1) * request.per_page
    
    # 获取总数
    total = await plugin_crud.count_by_user(
        mid=auth.mid,
        filter_type=request.filter_type
    )
    
    models = await plugin_crud.list_by_user(
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
            PluginListItemResponse(
                id=model.id,
                plugin_id=model.plugin_id,
                name=model.name,
                hook_type=model.hook_type,
                custom_action_id=model.custom_action_id,
                is_enabled=model.is_enabled,
                priority=model.priority,
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
    
    pagination = BasePaginationResp[PluginListItemResponse](
        page=request.page,
        per_page=request.per_page,
        total=total,
        items=items
    )
    
    return success_response(pagination)


# ============ Fork 功能 ============


@router.post("/community/actions/fork", summary="Fork 社区公开操作")
async def fork_community_action(
    request: ActionForkRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[ActionForkResponse]:
    """Fork 社区公开的自定义操作到自己的空间"""
    original = await action_crud.get_by_id(request.id)
    if not original:
        return error_response(404, "操作不存在")
    
    if not original.is_public:
        return error_response(403, "只能 Fork 公开的操作")
    
    try:
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


@router.post("/community/workflows/fork", summary="Fork 社区公开工作流")
async def fork_community_workflow(
    request: WorkflowForkRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowForkResponse]:
    """Fork 社区公开的工作流到自己的空间"""
    original = await workflow_crud.get_by_id(request.id)
    if not original:
        return error_response(404, "工作流不存在")
    
    if not original.is_public:
        return error_response(403, "只能 Fork 公开的工作流")
    
    try:
        model = await workflow_crud.fork(
            id=request.id,
            target_mid=auth.mid,
            new_name=request.new_name
        )
        
        if not model:
            return error_response(500, "Fork 失败")
        
        return success_response(
            WorkflowForkResponse(
                id=model.id,
                workflow_id=model.workflow_id,
                name=model.name,
                forked_from=original.name,
            ),
            message="Fork 成功"
        )
    except ValueError as e:
        return error_response(400, str(e))


@router.post("/community/plugins/fork", summary="Fork 社区公开插件")
async def fork_community_plugin(
    request: PluginForkRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[PluginForkResponse]:
    """Fork 社区公开的插件到自己的空间"""
    original = await plugin_crud.get_by_id(request.id)
    if not original:
        return error_response(404, "插件不存在")
    
    if not original.is_public:
        return error_response(403, "只能 Fork 公开的插件")
    
    try:
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


# ============ 点赞功能 ============


@router.post("/community/action/{action_id}/like", summary="点赞/取消点赞自定义操作")
async def like_action(
    action_id: int,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """点赞或取消点赞自定义操作"""
    result = await community_crud.toggle_like(
        mid=auth.mid,
        resource_type=1,  # CustomAction
        resource_id=action_id
    )
    
    if result is None:
        return error_response(404, "操作不存在")
    
    return success_response({
        "liked": result,
        "message": "点赞成功" if result else "取消点赞成功"
    })


@router.post("/community/workflow/{workflow_id}/like", summary="点赞/取消点赞工作流")
async def like_workflow(
    workflow_id: int,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """点赞或取消点赞工作流"""
    result = await community_crud.toggle_like(
        mid=auth.mid,
        resource_type=2,  # UserWorkflow
        resource_id=workflow_id
    )
    
    if result is None:
        return error_response(404, "工作流不存在")
    
    return success_response({
        "liked": result,
        "message": "点赞成功" if result else "取消点赞成功"
    })


@router.post("/community/plugin/{plugin_id}/like", summary="点赞/取消点赞插件")
async def like_plugin(
    plugin_id: int,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """点赞或取消点赞插件"""
    result = await community_crud.toggle_like(
        mid=auth.mid,
        resource_type=3,  # UserPlugin
        resource_id=plugin_id
    )
    
    if result is None:
        return error_response(404, "插件不存在")
    
    return success_response({
        "liked": result,
        "message": "点赞成功" if result else "取消点赞成功"
    })


# ============ 举报功能 ============


class ReportRequest(BasePaginationReq):
    """举报请求"""
    reason: int = 5  # 默认其他
    description: str = ""


@router.post("/community/action/{action_id}/report", summary="举报自定义操作")
async def report_action(
    action_id: int,
    request: ReportRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """举报自定义操作"""
    result = await community_crud.report(
        mid=auth.mid,
        resource_type=1,  # CustomAction
        resource_id=action_id,
        reason=request.reason,
        description=request.description
    )
    
    if result is None:
        return error_response(404, "操作不存在")
    
    if not result:
        return error_response(400, "您已举报过此操作")
    
    return success_response({
        "message": "举报成功，感谢您的反馈"
    })


@router.post("/community/workflow/{workflow_id}/report", summary="举报工作流")
async def report_workflow(
    workflow_id: int,
    request: ReportRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """举报工作流"""
    result = await community_crud.report(
        mid=auth.mid,
        resource_type=2,  # UserWorkflow
        resource_id=workflow_id,
        reason=request.reason,
        description=request.description
    )
    
    if result is None:
        return error_response(404, "工作流不存在")
    
    if not result:
        return error_response(400, "您已举报过此工作流")
    
    return success_response({
        "message": "举报成功，感谢您的反馈"
    })


@router.post("/community/plugin/{plugin_id}/report", summary="举报插件")
async def report_plugin(
    plugin_id: int,
    request: ReportRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """举报插件"""
    result = await community_crud.report(
        mid=auth.mid,
        resource_type=3,  # UserPlugin
        resource_id=plugin_id,
        reason=request.reason,
        description=request.description
    )
    
    if result is None:
        return error_response(404, "插件不存在")
    
    if not result:
        return error_response(400, "您已举报过此插件")
    
    return success_response({
        "message": "举报成功，感谢您的反馈"
    })


class UpdateReportRequest(BasePaginationReq):
    """修改举报请求"""
    report_id: int
    reason: int | None = None
    description: str | None = None


@router.post("/community/report/update", summary="修改自己的举报内容")
async def update_report(
    request: UpdateReportRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """修改自己的举报内容（仅允许修改理由和描述）
    
    Args:
        request: {
            "report_id": 举报记录ID,
            "reason": 新的举报理由（可选）,
            "description": 新的描述（可选）
        }
    
    Note:
        - 只能修改自己的举报
        - 已被管理员标记为无效的举报不能修改
        - 至少需要提供 reason 或 description
    """
    if request.reason is None and request.description is None:
        return error_response(400, "至少需要修改举报理由或描述")
    
    result = await community_crud.update_report(
        report_id=request.report_id,
        mid=auth.mid,
        reason=request.reason,
        description=request.description
    )
    
    if result is None:
        return error_response(404, "举报记录不存在")
    
    if result is False:
        return error_response(403, "您只能修改自己的举报，或该举报已被管理员处理")
    
    return success_response({
        "message": "举报内容已更新"
    })
