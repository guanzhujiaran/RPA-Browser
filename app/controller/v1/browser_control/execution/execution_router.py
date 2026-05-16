"""
自定义执行控制器

提供用户自定义操作、插件、工作流的 POST API（全部POST，避免缓存等问题）
"""
from loguru import logger
import time
from app.models.response_code import ResponseCode
from fastapi import Depends
from sqlmodel import SQLModel, Field
from typing import Any, List
import uuid
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.common.depends import BrowserReqInfo, BrowserReqAuthInfo
from fastapi import APIRouter
from app.services.execution.action_registry import action_registry
from app.services.execution.execution_engine import execution_engine, Workflow, WorkflowStep
from app.services.execution.crud_service import action_crud, workflow_crud
from app.models.core.workflow.models import ActionMetadataResponse
# 导入自定义执行模型
from app.models.workflow.models import (
    # 操作执行
    ActionExecuteRequest,
    BatchActionRequest,
    ActionResultResponse,
    # 自定义操作
    CustomActionCreateRequest,
    CustomActionUpdateRequest,
    CustomActionDetailResponse,
    CustomActionListItemResponse,
    # 工作流
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowListRequest,
    WorkflowExecuteRequest,
    WorkflowDetailResponse,
    WorkflowListItemResponse,
    WorkflowCreateResponse,
    WorkflowDuplicateResponse,
    WorkflowExecuteResponse,
    # 调试
    ActionPreviewRequest,
    ActionPreviewResponse,
    ActionValidateRequest,
    ActionValidateResponse,
    ExecuteStepRequest,
    ExecuteStepResponse,
    # 系统
    StepPreviewItem,
    ReloadActionsResponse,
)
# 核心执行模型（直接从 core 层导入）
from app.models.core.workflow.models import ActionMetadata
from ..base import new_execution_router

router = new_execution_router()


# ============ 系统预注册操作（只读，全 POST） ============


@router.post(BrowserControlRouterPath.actions_registered)
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


@router.post(BrowserControlRouterPath.actions_execute)
async def execute_action(
    request: ActionExecuteRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionResultResponse | None]:
    """执行单个操作"""
    # 🔑 调用 service 层方法
    result = await execution_engine.execute_action_with_session(
        mid=browser_info.auth_info.mid,
        browser_id=browser_info.browser_id,
        action_id=request.action_id,
        params=request.params,
        user_data=request.user_data,
        page_index=request.page_index,
    )
    
    return success_response(
        ActionResultResponse(
            success=result.success,
            data=result.data,
            error=result.error,
            execution_time=result.execution_time,
            action_id=result.action_id,
            action_name=result.action_name,
        )
    )



@router.post(BrowserControlRouterPath.actions_batch)
async def batch_execute(
    request: BatchActionRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[List[ActionResultResponse]]:
    """批量执行操作"""
    try:
        # 🔑 调用 service 层方法
        actions = [{"action_id": a.action_id, "params": a.params} for a in request.actions]
        results = await execution_engine.execute_batch_with_session(
            mid=browser_info.auth_info.mid,
            browser_id=browser_info.browser_id,
            actions=actions,
            parallel=request.parallel,
            user_data=request.user_data,
            page_index=request.page_index,
        )

        response = [
            ActionResultResponse(
                success=r.success,
                data=r.data,
                error=r.error,
                execution_time=r.execution_time,
                action_id=r.action_id,
                action_name=r.action_name,
            )
            for r in results
        ]
        return success_response(response)
    except ValueError as e:
        return error_response(str(e))


# ============ 自定义操作 CRUD（全 POST） ============


class IdRequest(SQLModel):
    """ID请求"""

    id: int


class IdListRequest(SQLModel):
    """ID列表请求"""

    skip: int = 0
    limit: int = 100


class CustomActionListRequest(SQLModel):
    """自定义操作列表请求"""
    skip: int = Field(default=0, description="跳过记录数")
    limit: int = Field(default=100, description="返回记录数")
    # 筛选条件
    filter_type: str = Field(default="all", description="筛选类型: all=全部, private=我的私有, public=我的公开, community=社区公开, verified=已认证")
    # 排序条件
    sort_by: str = Field(default="updated_at", description="排序字段: updated_at=最近更新, likes_count=最多点赞, created_at=最近创建, name=名称")
    sort_order: str = Field(default="desc", description="排序方向: desc=降序, asc=升序")


class WorkflowListRequest(SQLModel):
    """工作流列表请求"""
    skip: int = Field(default=0, description="跳过记录数")
    limit: int = Field(default=100, description="返回记录数")
    # 筛选条件
    filter_type: str = Field(default="all", description="筛选类型: all=全部, private=我的私有, public=我的公开, community=社区公开, verified=已认证")
    # 排序条件
    sort_by: str = Field(default="updated_at", description="排序字段: updated_at=最近更新, likes_count=最多点赞, created_at=最近创建, name=名称")
    sort_order: str = Field(default="desc", description="排序方向: desc=降序, asc=升序")


@router.post(BrowserControlRouterPath.custom_actions_list)
async def list_custom_actions(
    request: CustomActionListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[CustomActionListItemResponse]]:
    """获取用户自定义操作列表"""
    models = await action_crud.list_by_user(
        mid=auth.mid, 
        skip=request.skip, 
        limit=request.limit,
        filter_type=request.filter_type,
        sort_by=request.sort_by,
        sort_order=request.sort_order
    )
    response = [
        CustomActionListItemResponse(
            id=m.id,
            action_id=m.action_id,
            name=m.name,
            action_type=m.action_type,
            description=m.description,
            steps_count=len(m.steps) if m.is_composite else 0,
            is_enabled=m.is_enabled,
            is_public=m.is_public,
            likes_count=m.likes_count,
            reports_count=m.reports_count,
            is_verified=m.is_verified,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in models
    ]
    return success_response(response)


@router.post(BrowserControlRouterPath.custom_actions_reload)
async def reload_custom_actions(
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[ReloadActionsResponse]:
    """获取用户自定义操作数量（操作直接从数据库读取）"""
    mid = str(auth.mid)

    # 统计启用的操作数量
    models = await action_crud.list_by_user(mid=auth.mid)
    count = sum(1 for m in models if m.is_enabled and m.steps)

    return success_response(ReloadActionsResponse(loaded=count))


@router.post(BrowserControlRouterPath.custom_actions_get)
async def get_custom_action(
    request: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CustomActionDetailResponse]:
    """获取单个自定义操作"""
    model = await action_crud.get_by_id(request.id)
    if not model or model.mid != auth.mid:
        return error_response("操作不存在")
    
    # 获取关联的插件列表
    enabled_plugins = await action_crud.get_enabled_plugins(model.action_id)
    # 提取 plugin_id 列表用于响应
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
            tags=model.tags,
            user_data=model.user_data,
            enabled_plugins=enabled_plugin_ids,
            is_enabled=model.is_enabled,
            is_public=model.is_public,
            timeout=model.timeout,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.custom_actions_create)
async def create_custom_action(
    request: CustomActionCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CustomActionCreateRequest]:
    """创建自定义操作"""
    # 自动生成 action_id
    action_id = f"custom_{uuid.uuid4().hex[:8]}"

    # 转换 enabled_plugins 格式：从 List[str] 转为 List[Dict]
    enabled_plugins_data = [
        {"plugin_id": pid, "config_params": {}}
        for pid in request.enabled_plugins
    ] if request.enabled_plugins else None

    model = await action_crud.create(
        action_id=action_id,
        name=request.name,
        action_type=request.action_type,
        description=request.description,
        mid=auth.mid,
        parameters_schema=request.parameters_schema,
        steps=request.steps,
        tags=request.tags,
        user_data=request.user_data,
        is_composite=True,
        is_public=request.is_public,
        enabled_plugins=enabled_plugins_data,
    )

    return success_response(model)


@router.post(BrowserControlRouterPath.custom_actions_update)
async def update_custom_action(
    request: CustomActionUpdateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[str]:
    """更新自定义操作"""
    model = await action_crud.get_by_id(request.id)
    if not model or model.mid != auth.mid:
        return error_response("操作不存在")

    # 转换 enabled_plugins 格式：从 List[str] 转为 List[Dict]
    enabled_plugins_data = None
    if request.enabled_plugins is not None:
        enabled_plugins_data = [
            {"plugin_id": pid, "config_params": {}}
            for pid in request.enabled_plugins
        ]

    await action_crud.update(
        id=request.id,
        name=request.name,
        description=request.description,
        parameters_schema=request.parameters_schema,
        steps=request.steps,
        tags=request.tags,
        user_data=request.user_data,
        is_composite=True,
        timeout=request.timeout,
        is_public=request.is_public,
        enabled_plugins=enabled_plugins_data,
    )

    if request.is_enabled is not None:
        if request.is_enabled:
            await action_crud.enable(request.id)
        else:
            await action_crud.disable(request.id)

    return success_response("更新成功")


@router.post(BrowserControlRouterPath.custom_actions_delete)
async def delete_custom_action(
    request: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[str]:
    """删除自定义操作"""
    model = await action_crud.get_by_id(request.id)
    if not model or model.mid != auth.mid:
        return error_response("操作不存在")

    await action_crud.delete(request.id)
    return success_response("删除成功")


# ============ 工作流 CRUD（全 POST） ============


@router.post(BrowserControlRouterPath.workflows_list)
async def list_workflows(
    request: WorkflowListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[WorkflowListItemResponse]]:
    """获取用户工作流列表"""
    models = await workflow_crud.list_by_user(
        mid=auth.mid, 
        skip=request.skip, 
        limit=request.limit,
        filter_type=request.filter_type,
        sort_by=request.sort_by,
        sort_order=request.sort_order
    )
    response = [
        WorkflowListItemResponse(
            id=m.id,
            workflow_id=m.workflow_id,
            name=m.name,
            description=m.description,
            tags=m.tags,
            is_enabled=m.is_enabled,
            is_public=m.is_public,
            likes_count=m.likes_count,
            reports_count=m.reports_count,
            is_verified=m.is_verified,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in models
    ]
    return success_response(response)


@router.post(BrowserControlRouterPath.workflows_get)
async def get_workflow(
    request: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDetailResponse]:
    """获取单个工作流"""
    model = await workflow_crud.get_by_id(request.id)
    if not model or model.mid != auth.mid:
        return error_response("工作流不存在")
    
    # 获取关联的插件列表
    enabled_plugins = await workflow_crud.get_enabled_plugins(model.workflow_id)
    # 提取 plugin_id 列表用于响应
    enabled_plugin_ids = [p["plugin_id"] for p in enabled_plugins]
    
    return success_response(
        WorkflowDetailResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            version=model.version,
            steps=model.steps,
            on_error=model.on_error,
            description=model.description,
            tags=model.tags,
            user_data=model.user_data,
            enabled_plugins=enabled_plugin_ids,
            is_enabled=model.is_enabled,
            is_public=model.is_public,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.workflows_create)
async def create_workflow(
    request: WorkflowCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowCreateResponse]:
    """创建工作流

    workflow_id 自动生成 UUID，无需手动指定。
    name 必须指定，用于显示。
    """
    # 自动生成 workflow_id
    workflow_id = str(uuid.uuid4())

    # 转换 steps
    steps_data = []
    for step in request.steps:
        step_dict = {
            "action_id": step.action_id,
            "params": step.params,
            "loop_count": step.loop_count,
            "loop_while": step.loop_while,
            "loop_until": step.loop_until,
            "retry": step.retry,
            "condition": step.condition,
            "user_data": step.user_data,
        }
        steps_data.append(step_dict)

    # 转换 enabled_plugins 格式：从 List[str] 转为 List[Dict]
    enabled_plugins_data = [
        {"plugin_id": pid, "config_params": {}}
        for pid in request.enabled_plugins
    ] if request.enabled_plugins else None

    model = await workflow_crud.create(
        workflow_id=workflow_id,
        name=request.name,
        description=request.description,
        on_error=request.on_error,
        mid=auth.mid,
        steps=steps_data,
        tags=request.tags,
        user_data=request.user_data,
        is_public=False, # 默认私有
        enabled_plugins=enabled_plugins_data,
    )

    return success_response(
        WorkflowCreateResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
        )
    )


@router.post(BrowserControlRouterPath.workflows_update)
async def update_workflow(
    request: WorkflowUpdateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[str]:
    """更新工作流"""
    model = await workflow_crud.get_by_id(request.id)
    if not model or model.mid != auth.mid:
        return error_response("工作流不存在")

    # 转换 steps
    steps_data = None
    if request.steps is not None:
        steps_data = []
        for step in request.steps:
            step_dict = {
                "action_id": step.action_id,
                "params": step.params,
                "loop_count": step.loop_count,
                "loop_while": step.loop_while,
                "loop_until": step.loop_until,
                "retry": step.retry,
                "condition": step.condition,
                "user_data": step.user_data,
            }
            steps_data.append(step_dict)

    # 转换 enabled_plugins 格式：从 List[str] 转为 List[Dict]
    enabled_plugins_data = None
    if request.enabled_plugins is not None:
        enabled_plugins_data = [
            {"plugin_id": pid, "config_params": {}}
            for pid in request.enabled_plugins
        ]

    await workflow_crud.update(
        id=request.id,
        name=request.name,
        description=request.description,
        steps=steps_data,
        on_error=request.on_error,
        tags=request.tags,
        user_data=request.user_data,
        enabled_plugins=enabled_plugins_data,
    )

    if request.is_enabled is not None:
        if request.is_enabled:
            await workflow_crud.enable(request.id)
        else:
            await workflow_crud.disable(request.id)

    return success_response("更新成功")


@router.post(BrowserControlRouterPath.workflows_delete)
async def delete_workflow(
    request: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[str]:
    """删除工作流"""
    model = await workflow_crud.get_by_id(request.id)
    if not model or model.mid != auth.mid:
        return error_response("工作流不存在")

    await workflow_crud.delete(request.id)
    return success_response("删除成功")


@router.post(BrowserControlRouterPath.workflows_duplicate)
async def duplicate_workflow(
    request: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDuplicateResponse]:
    """复制工作流"""
    # 先检查原始工作流权限，防止越权
    original = await workflow_crud.get_by_id(request.id)
    if not original:
        return error_response("工作流不存在")
    if original.mid != auth.mid:
        return error_response("无权复制此工作流")

    # 权限检查通过后再复制
    model = await workflow_crud.duplicate(request.id)
    return success_response(
        WorkflowDuplicateResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
        )
    )


@router.post(BrowserControlRouterPath.workflows_execute)
async def execute_workflow(
    request: WorkflowExecuteRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[WorkflowExecuteResponse]:
    """执行工作流

    支持模板变量和自定义数据。
    """
    # 构建工作流步骤（支持嵌套）
    def build_steps(step_reqs):
        steps = []
        for step_req in step_reqs:
            children = None
            if hasattr(step_req, 'children') and step_req.children:
                children = build_steps(step_req.children)
            
            step = WorkflowStep(
                action_id=step_req.action_id,
                params=step_req.params,
                retry=step_req.retry,
                loop_count=step_req.loop_count,
                loop_while=step_req.loop_while,
                loop_until=step_req.loop_until,
                condition=step_req.condition,
                children=children,
            )
            steps.append(step)
        return steps

    workflow_steps = build_steps(request.steps)

    # 构建工作流
    workflow = Workflow(
        id=str(uuid.uuid4()),
        name=request.name or "临时工作流",
        steps=workflow_steps,
        on_error=request.on_error,
    )

    # 🔑 调用 service 层方法
    results = await execution_engine.execute_workflow_with_session(
        mid=browser_info.auth_info.mid,
        browser_id=browser_info.browser_id,
        workflow=workflow,
        user_data=request.user_data,
        page_index=request.page_index,
    )

    # 生成执行ID
    execution_id = str(uuid.uuid4())

    results_data = [
        {
            "success": r.success,
            "data": r.data,
            "error": r.error,
            "execution_time": r.execution_time,
            "action_id": r.action_id,
            "action_name": r.action_name,
        }
        for r in results
    ]

    return success_response(
        WorkflowExecuteResponse(
            execution_id=execution_id,
            results=results_data,
            summary={
                "total": len(results),
                "success": sum(1 for r in results if r.success),
                "failed": sum(1 for r in results if not r.success),
            },
        )
    )



# ============ 调试相关 API ============


@router.post(BrowserControlRouterPath.actions_preview)
async def preview_action_params(
    request: ActionPreviewRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionPreviewResponse]:
    """
    预览参数替换结果

    用于在执行前预览 {{param}} 模板参数的实际替换值。
    支持复合操作的逐步预览。
    """
    mid = str(browser_info.auth_info.mid)

    # 直接从数据库加载操作
    action_instance = await action_registry.create_action_for_user(
        request.action_id, mid
    )
    metadata = action_registry.get_action_metadata(request.action_id)

    if not metadata:
        return error_response(f"未找到操作: {request.action_id}")

    # 检查是否为组合操作
    composite = action_instance if hasattr(action_instance, "_steps") else None

    if composite and hasattr(composite, "_steps"):
        # 组合操作：逐步预览
        steps_preview = []
        found_params = set()

        for i, step in enumerate(composite._steps):
            original_params = step.get("params", {})
            replaced_params = composite._replace_params(original_params, request.params)

            # 收集被替换的参数名
            for key in request.params:
                for p_key, p_val in replaced_params.items():
                    if f"{{{{{key}}}}}" in str(p_val) or key in str(p_val):
                        found_params.add(key)

            steps_preview.append(
                StepPreviewItem(
                    step_index=i,
                    action_id=step["action_id"],
                    original_params=original_params,
                    replaced_params=replaced_params,
                )
            )

        return success_response(
            ActionPreviewResponse(
                action_id=request.action_id,
                action_name=metadata.name,
                is_composite=True,
                steps_preview=steps_preview,
                replaced_params={},
                found_params=list(found_params),
            )
        )
    else:
        # 普通操作：直接预览参数替换
        if action_instance and hasattr(action_instance, "_replace_params"):
            replaced_params = action_instance._replace_params(
                request.params, request.params
            )
        else:
            replaced_params = request.params

        return success_response(
            ActionPreviewResponse(
                action_id=request.action_id,
                action_name=metadata.name,
                is_composite=False,
                steps_preview=[],
                replaced_params=replaced_params,
                found_params=[],
            )
        )


@router.post(BrowserControlRouterPath.actions_validate)
async def validate_action_params(
    request: ActionValidateRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionValidateResponse]:
    """
    验证操作参数

    验证参数是否符合操作的参数定义要求。
    """
    mid = str(browser_info.auth_info.mid)
    # 尝试获取操作
    await action_registry.create_action_for_user(request.action_id, mid)
    metadata = action_registry.get_action_metadata(request.action_id)

    if not metadata:
        return error_response(f"未找到操作: {request.action_id}")

    missing_params = []
    invalid_params = []
    errors = []

    # 检查必需参数
    for param in metadata.parameters:
        if param.required and param.name not in request.params:
            missing_params.append(param.name)
        elif param.name in request.params:
            value = request.params[param.name]
            # 类型检查
            if param.type != Any and param.type is not None:
                expected_type = (
                    param.type if isinstance(param.type, str) else str(param.type)
                )
                actual_type = type(value).__name__
                if not isinstance(
                    value,
                    (
                        (param.type, type(None))
                        if isinstance(param.type, type)
                        else (str, int, float, bool, list, dict)
                    ),
                ):
                    # 允许的类型兼容
                    pass

            # 自定义验证器
            if param.validator and not param.validator(value):
                invalid_params.append(param.name)
                errors.append(f"参数 {param.name} 验证失败: {param.description}")

    valid = len(missing_params) == 0 and len(invalid_params) == 0

    return success_response(
        ActionValidateResponse(
            valid=valid,
            action_id=request.action_id,
            action_name=metadata.name,
            missing_params=missing_params,
            invalid_params=invalid_params,
            errors=errors,
        )
    )


@router.post(BrowserControlRouterPath.actions_execute_step)
async def execute_action_step(
    request: ExecuteStepRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ExecuteStepResponse]:
    """
    单步执行操作

    用于复合操作的逐步执行或调试。
    - 如果 action_id 是复合操作，执行指定 step_index 的步骤
    - 如果 action_id 是普通操作，执行该操作
    """
    try:
        # 🔑 调用 service 层方法
        step_index, action_id, action_name, result = await execution_engine.execute_action_step_with_session(
            mid=browser_info.auth_info.mid,
            browser_id=browser_info.browser_id,
            action_id=request.action_id,
            params=request.params,
            step_index=request.step_index,
            page_index=request.page_index,
        )
        
        return success_response(
            ExecuteStepResponse(
                step_index=step_index,
                action_id=action_id,
                action_name=action_name,
                result=ActionResultResponse(
                    success=result.success,
                    data=result.data,
                    error=result.error,
                    execution_time=result.execution_time,
                    action_id=result.action_id,
                    action_name=result.action_name,
                ),
            )
        )
    except ValueError as e:
        return error_response(str(e))


# ============ 社区互动功能 (Community Interaction) ============

@router.post("/custom_actions/{action_id}/like")
async def like_custom_action(
    action_id: int,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[str]:
    """点赞自定义动作"""
    # TODO: 在 crud_service 中实现 increment_likes 方法
    # await action_crud.increment_likes(action_id)
    return success_response("点赞成功")

@router.post("/workflows/{workflow_id}/fork")
async def fork_workflow(
    workflow_id: int,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDuplicateResponse]:
    """克隆（Fork）公开工作流到自己的空间"""
    original = await workflow_crud.get_by_id(workflow_id)
    if not original:
        return error_response("工作流不存在")
    
    # 如果是自己的，直接走 duplicate 逻辑；如果是公开的，则复制一份
    if original.mid == auth.mid:
        model = await workflow_crud.duplicate(workflow_id)
    else:
        # 这里需要 crud_service 支持跨用户复制
        # model = await workflow_crud.fork(workflow_id, auth.mid)
        return error_response("暂不支持克隆他人工作流（待实现）")
        
    return success_response(
        WorkflowDuplicateResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
        )
    )
