"""
Workflow 管理路由

提供工作流（Workflow）的 CRUD 和执行 API
"""
from loguru import logger
from typing import Any, List
import uuid
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from fastapi import APIRouter, Depends
from app.services.execution.crud_service import workflow_crud
from app.models.workflow.models import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowListRequest,
    WorkflowExecuteRequest,
    WorkflowDetailResponse,
    WorkflowListItemResponse,
    WorkflowCreateResponse,
    WorkflowDuplicateResponse,
    WorkflowForkRequest,
    WorkflowForkResponse,
    WorkflowExecuteResponse,
    WorkflowStepExecuteRequest,
    WorkflowStepExecuteResponse,
)
from app.models.base.base_sqlmodel import BasePaginationResp
from ..base import new_workflow_router

router = new_workflow_router()


# ============ 工作流管理（Workflow） ============


@router.post(BrowserControlRouterPath.workflows_create, summary="创建工作流")
async def create_workflow(
    request: WorkflowCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowCreateResponse]:
    """创建用户工作流
    
    工作流由多个步骤组成，可以包含操作、插件、控制流等
    """
    # 生成唯一的 workflow_id
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    
    model = await workflow_crud.create(
        workflow_id=workflow_id,
        name=request.name,
        mid=auth.mid,
        description=request.description,
        steps=request.steps,
        enabled_plugins=request.enabled_plugins or [],
        tags=request.tags or [],
        icon=request.icon,
        color=request.color,
        error_handling=request.error_handling,
        max_retries=request.max_retries,
        timeout=request.timeout,
    )
    
    return success_response(
        WorkflowCreateResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            message="工作流创建成功",
        )
    )


@router.post(BrowserControlRouterPath.workflows_list, summary="获取工作流列表")
async def list_workflows(
    request: WorkflowListRequest = None,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[WorkflowListItemResponse]]:
    """获取当前用户的工作流列表
    
    支持分页、筛选和排序
    """
    if request is None:
        request = WorkflowListRequest()
    
    # 计算 skip
    skip = (request.page - 1) * request.per_page
    
    # 获取总数
    total = await workflow_crud.count_by_user(
        mid=auth.mid,
        filter_type=request.filter_type
    )
    
    # 获取列表数据
    models = await workflow_crud.list_by_user(
        mid=auth.mid,
        skip=skip,
        limit=request.per_page,
        filter_type=request.filter_type,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
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
    
    # 构建分页响应
    pagination = BasePaginationResp[WorkflowListItemResponse](
        page=request.page,
        per_page=request.per_page,
        total=total,
        items=items
    )
    
    return success_response(pagination)


@router.post(BrowserControlRouterPath.workflows_get, summary="获取工作流详情")
async def get_workflow_detail(
    request: dict,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDetailResponse]:
    """获取工作流详情
    
    Args:
        request: {"id": <工作流ID>}
    """
    workflow_id = request.get("id")
    if not workflow_id:
        return error_response(400, "缺少工作流ID")
    
    model = await workflow_crud.get_by_id(workflow_id)
    if not model or model.mid != auth.mid:
        return error_response(404, "工作流不存在")
    
    # 获取关联的插件列表
    enabled_plugins = await workflow_crud.get_enabled_plugins(model.workflow_id)
    enabled_plugin_ids = [p["plugin_id"] for p in enabled_plugins]
    
    return success_response(
        WorkflowDetailResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            description=model.description,
            steps=model.steps,
            enabled_plugins=enabled_plugin_ids,
            tags=model.tags,
            icon=model.icon,
            color=model.color,
            error_handling=model.error_handling,
            max_retries=model.max_retries,
            timeout=model.timeout,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.workflows_update, summary="更新工作流")
async def update_workflow(
    request: WorkflowUpdateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDetailResponse]:
    """更新工作流"""
    model = await workflow_crud.update(
        id=request.id,
        name=request.name,
        description=request.description,
        steps=request.steps,
        enabled_plugins=request.enabled_plugins,
        tags=request.tags,
        icon=request.icon,
        color=request.color,
        error_handling=request.error_handling,
        max_retries=request.max_retries,
        timeout=request.timeout,
    )
    
    if not model or model.mid != auth.mid:
        return error_response(404, "工作流不存在或无权限")
    
    # 获取关联的插件列表
    enabled_plugins = await workflow_crud.get_enabled_plugins(model.workflow_id)
    enabled_plugin_ids = [p["plugin_id"] for p in enabled_plugins]
    
    return success_response(
        WorkflowDetailResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            description=model.description,
            steps=model.steps,
            enabled_plugins=enabled_plugin_ids,
            tags=model.tags,
            icon=model.icon,
            color=model.color,
            error_handling=model.error_handling,
            max_retries=model.max_retries,
            timeout=model.timeout,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.workflows_delete, summary="删除工作流")
async def delete_workflow(
    request: dict,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """删除工作流
    
    Args:
        request: {"id": <工作流ID>}
    """
    workflow_id = request.get("id")
    if not workflow_id:
        return error_response(400, "缺少工作流ID")
    
    model = await workflow_crud.get_by_id(workflow_id)
    if not model or model.mid != auth.mid:
        return error_response(404, "工作流不存在或无权限")
    
    success = await workflow_crud.delete(workflow_id)
    if success:
        return success_response({"message": "删除成功"})
    else:
        return error_response(500, "删除失败")


@router.post(BrowserControlRouterPath.workflows_duplicate, summary="复制工作流")
async def duplicate_workflow(
    request: dict,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDuplicateResponse]:
    """复制工作流
    
    Args:
        request: {"id": <工作流ID>, "new_name": <新名称>}
    """
    workflow_id = request.get("id")
    new_name = request.get("new_name")
    
    if not workflow_id:
        return error_response(400, "缺少工作流ID")
    if not new_name:
        return error_response(400, "缺少新名称")
    
    # 获取原工作流
    original = await workflow_crud.get_by_id(workflow_id)
    if not original or original.mid != auth.mid:
        return error_response(404, "工作流不存在或无权限")
    
    # 创建副本
    new_workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    
    model = await workflow_crud.create(
        workflow_id=new_workflow_id,
        name=new_name,
        mid=auth.mid,
        description=f"复制自: {original.name}",
        steps=original.steps,
        enabled_plugins=[],  # 需要重新配置插件
        tags=original.tags,
        icon=original.icon,
        color=original.color,
        error_handling=original.error_handling,
        max_retries=original.max_retries,
        timeout=original.timeout,
    )
    
    return success_response(
        WorkflowDuplicateResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            message="工作流复制成功",
        )
    )


@router.post("/workflows/fork", summary="Fork 工作流（类似 GitHub）", response_model=StandardResponse[WorkflowForkResponse])
async def fork_workflow(
    request: WorkflowForkRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowForkResponse]:
    """Fork 工作流
    
    - 如果是自己的工作流：允许无条件 Fork（类似“创建副本”）
    - 如果是别人的工作流：仅允许 Fork 公开的工作流（类似 GitHub）
    
    Args:
        request: {"id": <工作流ID>, "new_name": <新名称（可选）>}
    """
    # 获取原工作流
    original = await workflow_crud.get_by_id(request.id)
    if not original:
        return error_response(404, "工作流不存在")
    
    # 检查权限：如果是别人的工作流，必须是公开的
    if original.mid != auth.mid and not original.is_public:
        return error_response(403, "只能 Fork 公开的工作流或自己的工作流")
    
    try:
        # 执行 Fork
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


@router.get("/workflows/{id}/forks", summary="获取工作流的所有 Fork 版本")
async def get_workflow_forks(
    id: int,
    skip: int = 0,
    limit: int = 50,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[WorkflowListItemResponse]]:
    """获取某工作流的所有 Fork 版本列表"""
    original = await workflow_crud.get_by_id(id)
    if not original:
        return error_response(404, "工作流不存在")
    
    forks = await workflow_crud.list_forks(id, skip, limit)
    
    items = [
        WorkflowListItemResponse(
            id=f.id,
            workflow_id=f.workflow_id,
            name=f.name,
            description=f.description,
            tags=f.tags or [],
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


@router.post(BrowserControlRouterPath.workflows_execute, summary="执行工作流")
async def execute_workflow(
    request: WorkflowExecuteRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowExecuteResponse]:
    """执行工作流
    
    在指定浏览器会话中执行工作流
    """
    # TODO: 实现工作流执行逻辑
    # 这里需要集成 execution_engine 来执行工作流
    
    return success_response(
        WorkflowExecuteResponse(
            execution_id=f"exec_{uuid.uuid4().hex[:12]}",
            status="started",
            message="工作流开始执行",
        )
    )


@router.post(BrowserControlRouterPath.workflows_execute_step, summary="单步执行工作流")
async def execute_workflow_step(
    request: WorkflowStepExecuteRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowStepExecuteResponse]:
    """单步执行工作流
    
    执行工作流中的指定步骤，用于调试和逐步执行
    """
    from app.services.execution.rpa_operation_service import RPAOperationService
    from app.models.browser_control.page_model import BrowserPageActivateRequest
    
    try:
        # 验证步骤索引
        if request.step_index < 0 or request.step_index >= len(request.steps):
            return error_response(
                code=400,
                msg=f"步骤索引 {request.step_index} 超出范围（总共 {len(request.steps)} 步）"
            )
        
        # 获取要执行的步骤
        step = request.steps[request.step_index]
        
        logger.info(f"[Workflow Step Execute] 执行步骤 {request.step_index + 1}/{len(request.steps)}: {step.action_id}")
        
        # 激活页面（如果指定了 page_index）
        if request.page_index is not None:
            try:
                from ..page_management import page_crud
                pages = await page_crud.list_by_browser(request.browser_id)
                if request.page_index < len(pages):
                    page = pages[request.page_index]
                    await page_crud.activate_page(page.page_id)
                    logger.info(f"[Workflow Step Execute] 已激活页面: {page.title}")
            except Exception as e:
                logger.warning(f"[Workflow Step Execute] 激活页面失败: {e}")
        
        # 执行动作
        start_time = __import__('time').time()
        service = RPAOperationService()
        result = await service.execute_action(
            browser_id=request.browser_id,
            action_id=step.action_id,
            params=step.params or {},
            user_data=request.user_data
        )
        duration = (__import__('time').time() - start_time) * 1000  # 转换为毫秒
        
        logger.info(f"[Workflow Step Execute] 步骤执行完成: success={result.get('success')}, duration={duration:.2f}ms")
        
        return success_response(
            WorkflowStepExecuteResponse(
                success=result.get('success', False),
                step_index=request.step_index,
                action_id=step.action_id,
                result=result.get('data'),
                error=result.get('error'),
                duration=duration,
                current_step=request.step_index,
                total_steps=len(request.steps)
            )
        )
    
    except Exception as e:
        logger.error(f"[Workflow Step Execute] 执行失败: {e}")
        return error_response(
            code=500,
            msg=f"执行步骤失败: {str(e)}"
        )
