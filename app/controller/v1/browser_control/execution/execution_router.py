"""
自定义执行控制器

提供用户自定义操作、插件、工作流的 POST API（全部POST，避免缓存等问题）
"""

from fastapi import Depends
from sqlmodel import SQLModel
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
from app.services.execution.execution_engine import execution_engine
from app.services.execution.crud_service import action_crud, workflow_crud

# 导入自定义执行模型（直接从源文件导入）
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
    ActionParameterResponse,
    ActionMetadataResponse,
    ReloadActionsResponse,
)
from ..base import new_execution_router

router = new_execution_router()


# ============ 系统预注册操作（只读，全 POST） ============


@router.post(BrowserControlRouterPath.actions_registered)
async def list_registered_actions() -> StandardResponse[List[ActionMetadataResponse]]:
    """获取系统预注册操作列表（公开，只读）"""
    actions = action_registry.get_all_actions()
    response = []
    for a in actions:
        params = []
        for p in getattr(a, "parameters", []):
            params.append(
                ActionParameterResponse(
                    name=p.name,
                    type=str(p.type) if hasattr(p, "type") else "",
                    required=p.required,
                    default=p.default,
                    description=p.description,
                )
            )
        response.append(
            ActionMetadataResponse(
                id=a.id,
                name=a.name,
                type=str(a.type) if hasattr(a, "type") else "",
                description=a.description,
                parameters=params,
                timeout=getattr(a, "timeout", 30000),
                retry_on_error=getattr(a, "retry_on_error", False),
                retry_times=getattr(a, "retry_times", 0),
                retry_delay=getattr(a, "retry_delay", 1.0),
                requires_browser=getattr(a, "requires_browser", True),
            )
        )
    return success_response(response)


@router.post(BrowserControlRouterPath.actions_execute)
async def execute_action(
    req: ActionExecuteRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionResultResponse]:
    """执行单个操作"""
    session_key = LiveService._get_session_key(
        browser_info.auth_info.mid, browser_info.browser_id
    )
    entry = LiveService.browser_sessions.get(session_key)
    if not entry:
        return error_response("浏览器不存在或未运行")

    mid = str(browser_info.auth_info.mid)
    page = await entry.plugined_session.get_current_page()
    browser = entry.plugined_session.browser_context.browser
    result = await execution_engine.execute_action(
        session_id=browser_info.browser_id,
        browser_id=browser_info.browser_id,
        page=page,
        browser=browser,
        action_id=req.action_id,
        params=req.params,
        mid=mid,
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
    req: BatchActionRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[List[ActionResultResponse]]:
    """批量执行操作"""
    session_key = LiveService._get_session_key(
        browser_info.auth_info.mid, browser_info.browser_id
    )
    entry = LiveService.browser_sessions.get(session_key)
    if not entry:
        return error_response("浏览器不存在或未运行")

    mid = str(browser_info.auth_info.mid)
    page = await entry.plugined_session.get_current_page()
    browser = entry.plugined_session.browser_context.browser
    actions = [{"action_id": a.action_id, "params": a.params} for a in req.actions]
    results = await execution_engine.execute_batch(
        session_id=browser_info.browser_id,
        browser_id=browser_info.browser_id,
        page=page,
        browser=browser,
        actions=actions,
        parallel=req.parallel,
        mid=mid,
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


# ============ 自定义操作 CRUD（全 POST） ============


class IdRequest(SQLModel):
    """ID请求"""
    id: int


class IdListRequest(SQLModel):
    """ID列表请求"""
    skip: int = 0
    limit: int = 100


@router.post(BrowserControlRouterPath.custom_actions_list)
async def list_custom_actions(
    req: IdListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[CustomActionListItemResponse]]:
    """获取用户自定义操作列表"""
    models = await action_crud.list_by_user(
        mid=auth.mid, skip=req.skip, limit=req.limit
    )
    response = [
        CustomActionListItemResponse(
            id=m.id,
            action_id=m.action_id,
            name=m.name,
            action_type=m.action_type,
            description=m.description,
            steps_count=len(m.get_steps()) if m.is_composite else 0,
            is_enabled=m.is_enabled,
            created_at=m.created_at,
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
    models = await action_crud.list_by_user(mid=mid)
    count = sum(1 for m in models if m.is_enabled and m.get_steps())

    return success_response(ReloadActionsResponse(loaded=count))


@router.post(BrowserControlRouterPath.custom_actions_get)
async def get_custom_action(
    req: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CustomActionDetailResponse]:
    """获取单个自定义操作"""
    model = await action_crud.get_by_id(req.id)
    if not model or model.mid != auth.mid:
        return error_response("操作不存在")
    return success_response(
        CustomActionDetailResponse(
            id=model.id,
            action_id=model.action_id,
            name=model.name,
            version=model.version,
            action_type=model.action_type,
            description=model.description,
            parameters_schema=model.get_parameters_schema(),
            steps=model.get_steps(),
            tags=model.get_tags(),
            user_data=model.get_user_data(),
            is_enabled=model.is_enabled,
            timeout=model.timeout,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.custom_actions_create)
async def create_custom_action(
    req: CustomActionCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CustomActionCreateRequest]:
    """创建自定义操作"""
    # 自动生成 action_id
    action_id = f"custom_{uuid.uuid4().hex[:8]}"

    model = await action_crud.create(
        action_id=action_id,
        name=req.name,
        action_type=req.action_type,
        description=req.description,
        mid=auth.mid,
        parameters_schema=req.parameters_schema,
        steps=req.steps,
        is_composite=True,
        code=req.code,
    )

    return success_response(model)


@router.post(BrowserControlRouterPath.custom_actions_update)
async def update_custom_action(
    req: CustomActionUpdateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[str]:
    """更新自定义操作"""
    model = await action_crud.get_by_id(req.id)
    if not model or model.mid != auth.mid:
        return error_response("操作不存在")

    await action_crud.update(
        id=req.id,
        name=req.name,
        description=req.description,
        parameters_schema=req.parameters_schema,
        steps=req.steps,
        is_composite=True,
        code=req.code,
        timeout=req.timeout,
    )

    if req.is_enabled is not None:
        if req.is_enabled:
            await action_crud.enable(req.id)
        else:
            await action_crud.disable(req.id)

    return success_response("更新成功")


@router.post(BrowserControlRouterPath.custom_actions_delete)
async def delete_custom_action(
    req: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[str]:
    """删除自定义操作"""
    model = await action_crud.get_by_id(req.id)
    if not model or model.mid != auth.mid:
        return error_response("操作不存在")

    await action_crud.delete(req.id)
    return success_response("删除成功")


# ============ 工作流 CRUD（全 POST） ============


@router.post(BrowserControlRouterPath.workflows_list)
async def list_workflows(
    req: IdListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[WorkflowListItemResponse]]:
    """获取用户工作流列表"""
    models = await workflow_crud.list_by_user(
        mid=auth.mid, skip=req.skip, limit=req.limit
    )
    response = [
        WorkflowListItemResponse(
            id=m.id,
            workflow_id=m.workflow_id,
            name=m.name,
            description=m.description,
            tags=m.get_tags(),
            is_enabled=m.is_enabled,
            created_at=m.created_at,
        )
        for m in models
    ]
    return success_response(response)


@router.post(BrowserControlRouterPath.workflows_get)
async def get_workflow(
    req: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDetailResponse]:
    """获取单个工作流"""
    model = await workflow_crud.get_by_id(req.id)
    if not model or model.mid != auth.mid:
        return error_response("工作流不存在")
    return success_response(
        WorkflowDetailResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            version=model.version,
            steps=model.get_steps(),
            on_error=model.on_error,
            description=model.description,
            tags=model.get_tags(),
            user_data=model.get_user_data(),
            is_enabled=model.is_enabled,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.workflows_create)
async def create_workflow(
    req: WorkflowCreateRequest,
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
    for i, step in enumerate(req.steps):
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

    model = await workflow_crud.create(
        workflow_id=workflow_id,
        name=req.name,
        description=req.description,
        on_error=req.on_error,
        mid=auth.mid,
        steps=steps_data,
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
    req: WorkflowUpdateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[str]:
    """更新工作流"""
    model = await workflow_crud.get_by_id(req.id)
    if not model or model.mid != auth.mid:
        return error_response("工作流不存在")

    # 转换 steps
    steps_data = None
    if req.steps is not None:
        steps_data = []
        for step in req.steps:
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

    await workflow_crud.update(
        id=req.id,
        name=req.name,
        description=req.description,
        steps=steps_data,
        on_error=req.on_error,
        tags=req.tags,
    )

    if req.is_enabled is not None:
        if req.is_enabled:
            await workflow_crud.enable(req.id)
        else:
            await workflow_crud.disable(req.id)

    return success_response("更新成功")


@router.post(BrowserControlRouterPath.workflows_delete)
async def delete_workflow(
    req: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[str]:
    """删除工作流"""
    model = await workflow_crud.get_by_id(req.id)
    if not model or model.mid != auth.mid:
        return error_response("工作流不存在")

    await workflow_crud.delete(req.id)
    return success_response("删除成功")


@router.post(BrowserControlRouterPath.workflows_duplicate)
async def duplicate_workflow(
    req: IdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDuplicateResponse]:
    """复制工作流"""
    # 先检查原始工作流权限，防止越权
    original = await workflow_crud.get_by_id(req.id)
    if not original:
        return error_response("工作流不存在")
    if original.mid != auth.mid:
        return error_response("无权复制此工作流")

    # 权限检查通过后再复制
    model = await workflow_crud.duplicate(req.id)
    return success_response(
        WorkflowDuplicateResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
        )
    )


@router.post(BrowserControlRouterPath.workflows_execute)
async def execute_workflow(
    req: WorkflowExecuteRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[WorkflowExecuteResponse]:
    """执行工作流

    支持模板变量和自定义数据。
    """
    from app.services.execution.execution_engine import Workflow, WorkflowStep

    session_key = LiveService._get_session_key(
        browser_info.auth_info.mid, browser_info.browser_id
    )
    entry = LiveService.browser_sessions.get(session_key)
    if not entry:
        return error_response("浏览器不存在或未运行")

    # 构建工作流步骤
    workflow_steps = []
    for step_req in req.steps:
        step = WorkflowStep(
            action_id=step_req.action_id,
            params=step_req.params,
            retry=step_req.retry,
            loop_count=step_req.loop_count,
            loop_while=step_req.loop_while,
            loop_until=step_req.loop_until,
            condition=None,
        )
        workflow_steps.append(step)

    # 构建工作流
    workflow = Workflow(
        id=str(uuid.uuid4()),
        name=req.name or "临时工作流",
        steps=workflow_steps,
        on_error=req.on_error,
    )

    # 生成执行ID
    execution_id = str(uuid.uuid4())

    # 获取用户数据
    user_data = req.user_data or {}

    # 执行
    mid = str(browser_info.auth_info.mid)
    page = await entry.plugined_session.get_current_page()
    browser = entry.plugined_session.browser_context.browser
    results = await execution_engine.execute_workflow(
        session_id=browser_info.browser_id,
        browser_id=browser_info.browser_id,
        page=page,
        browser=browser,
        workflow=workflow,
        user_data=user_data,
        mid=mid,
    )

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
    req: ActionPreviewRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionPreviewResponse]:
    """
    预览参数替换结果

    用于在执行前预览 {{param}} 模板参数的实际替换值。
    支持复合操作的逐步预览。
    """
    mid = str(browser_info.auth_info.mid)

    # 直接从数据库加载操作
    action_instance = await action_registry.create_action_for_user(req.action_id, mid)
    metadata = action_registry.get_action_metadata(req.action_id)

    if not metadata:
        return error_response(f"未找到操作: {req.action_id}")

    # 检查是否为组合操作
    composite = action_instance if hasattr(action_instance, "_steps") else None

    if composite and hasattr(composite, "_steps"):
        # 组合操作：逐步预览
        steps_preview = []
        found_params = set()

        for i, step in enumerate(composite._steps):
            original_params = step.get("params", {})
            replaced_params = composite._replace_params(original_params, req.params)

            # 收集被替换的参数名
            for key in req.params:
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
                action_id=req.action_id,
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
            replaced_params = action_instance._replace_params(req.params, req.params)
        else:
            replaced_params = req.params

        return success_response(
            ActionPreviewResponse(
                action_id=req.action_id,
                action_name=metadata.name,
                is_composite=False,
                steps_preview=[],
                replaced_params=replaced_params,
                found_params=[],
            )
        )


@router.post(BrowserControlRouterPath.actions_validate)
async def validate_action_params(
    req: ActionValidateRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionValidateResponse]:
    """
    验证操作参数

    验证参数是否符合操作的参数定义要求。
    """
    mid = str(browser_info.auth_info.mid)
    # 尝试获取操作
    await action_registry.create_action_for_user(req.action_id, mid)
    metadata = action_registry.get_action_metadata(req.action_id)

    if not metadata:
        return error_response(f"未找到操作: {req.action_id}")

    missing_params = []
    invalid_params = []
    errors = []

    # 检查必需参数
    for param in metadata.parameters:
        if param.required and param.name not in req.params:
            missing_params.append(param.name)
        elif param.name in req.params:
            value = req.params[param.name]
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
            action_id=req.action_id,
            action_name=metadata.name,
            missing_params=missing_params,
            invalid_params=invalid_params,
            errors=errors,
        )
    )


@router.post(BrowserControlRouterPath.actions_execute_step)
async def execute_action_step(
    req: ExecuteStepRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ExecuteStepResponse]:
    """
    单步执行操作

    用于复合操作的逐步执行或调试。
    - 如果 action_id 是复合操作，执行指定 step_index 的步骤
    - 如果 action_id 是普通操作，执行该操作
    """
    session_key = LiveService._get_session_key(
        browser_info.auth_info.mid, browser_info.browser_id
    )
    entry = LiveService.browser_sessions.get(session_key)
    if not entry:
        return error_response("浏览器不存在或未运行")

    mid = str(browser_info.auth_info.mid)
    page = await entry.plugined_session.get_current_page()
    browser = entry.plugined_session.browser_context.browser

    # 从数据库加载操作
    action_instance = await action_registry.create_action_for_user(req.action_id, mid)
    metadata = action_registry.get_action_metadata(req.action_id)
    if not metadata:
        return error_response(f"未找到操作: {req.action_id}")

    # 检查是否为组合操作
    composite = action_instance if hasattr(action_instance, "_steps") else None

    if composite and hasattr(composite, "_steps"):
        # 组合操作：执行指定步骤
        steps = composite._steps
        if req.step_index < 0 or req.step_index >= len(steps):
            return error_response(
                f"步骤索引 {req.step_index} 超出范围 (0-{len(steps)-1})"
            )

        step = steps[req.step_index]
        # 替换参数
        step_params = composite._replace_params(step.get("params", {}), req.params)

        # 执行子操作（子操作也要传入 mid）
        result = await execution_engine.execute_action(
            session_id=browser_info.browser_id,
            browser_id=browser_info.browser_id,
            page=page,
            browser=browser,
            action_id=step["action_id"],
            params=step_params,
            mid=mid,
        )

        return success_response(
            ExecuteStepResponse(
                step_index=req.step_index,
                action_id=step["action_id"],
                action_name=metadata.name,
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
    else:
        # 普通操作：直接执行
        result = await execution_engine.execute_action(
            session_id=browser_info.browser_id,
            browser_id=browser_info.browser_id,
            page=page,
            browser=browser,
            action_id=req.action_id,
            params=req.params,
            mid=mid,
        )

        return success_response(
            ExecuteStepResponse(
                step_index=0,
                action_id=req.action_id,
                action_name=metadata.name,
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
