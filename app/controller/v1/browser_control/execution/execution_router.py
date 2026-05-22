"""
执行引擎路由

提供操作执行相关的 API（执行、批量执行、调试等）
自定义操作和工作流的 CRUD 已迁移到 action_router.py 和 workflow_router.py
"""
from loguru import logger
from typing import Any, List
import uuid
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.common.depends import BrowserReqAuthInfo
from app.services.execution.action_registry import action_registry
from app.services.execution.execution_engine import execution_engine, Workflow, WorkflowStep
from app.services.execution.crud_service import workflow_crud
from app.models.core.workflow.models import ActionMetadataResponse
# 导入执行模型
from app.models.workflow.models import (
    ActionExecuteRequest,
    BatchActionRequest,
    ActionResultResponse,
    WorkflowExecuteRequest,
    WorkflowExecuteResponse,
    WorkflowDuplicateResponse,
    ActionPreviewRequest,
    ActionPreviewResponse,
    ActionValidateRequest,
    ActionValidateResponse,
    ExecuteStepRequest,
    ExecuteStepResponse,
    StepPreviewItem,
)
from ..base import new_execution_router

router = new_execution_router()


# ============ 操作执行 API ============


@router.post(BrowserControlRouterPath.actions_execute, summary="执行单个操作")
async def execute_action(
    request: ActionExecuteRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionResultResponse | None]:
    """执行单个操作"""
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


@router.post(BrowserControlRouterPath.actions_batch, summary="批量执行操作")
async def batch_execute(
    request: BatchActionRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[List[ActionResultResponse]]:
    """批量执行操作"""
    try:
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


# ============ 工作流执行 API ============


@router.post(BrowserControlRouterPath.workflows_execute, summary="执行工作流")
async def execute_workflow(
    request: WorkflowExecuteRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[WorkflowExecuteResponse]:
    """执行工作流
    
    支持模板变量和自定义数据。
    """
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

    workflow = Workflow(
        id=str(uuid.uuid4()),
        name=request.name or "临时工作流",
        steps=workflow_steps,
        on_error=request.on_error,
    )

    results = await execution_engine.execute_workflow_with_session(
        mid=browser_info.auth_info.mid,
        browser_id=browser_info.browser_id,
        workflow=workflow,
        user_data=request.user_data,
        page_index=request.page_index,
    )

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


@router.post(BrowserControlRouterPath.actions_preview, summary="预览参数替换结果")
async def preview_action_params(
    request: ActionPreviewRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionPreviewResponse]:
    """预览参数替换结果
    
    用于在执行前预览 {{param}} 模板参数的实际替换值。
    支持复合操作的逐步预览。
    """
    mid = str(browser_info.auth_info.mid)

    action_instance = await action_registry.create_action_for_user(
        request.action_id, mid
    )
    metadata = action_registry.get_action_metadata(request.action_id)

    if not metadata:
        return error_response(f"未找到操作: {request.action_id}")

    composite = action_instance if hasattr(action_instance, "_steps") else None

    if composite and hasattr(composite, "_steps"):
        steps_preview = []
        found_params = set()

        for i, step in enumerate(composite._steps):
            original_params = step.get("params", {})
            replaced_params = composite._replace_params(original_params, request.params)

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


@router.post(BrowserControlRouterPath.actions_validate, summary="验证操作参数")
async def validate_action_params(
    request: ActionValidateRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionValidateResponse]:
    """验证操作参数
    
    验证参数是否符合操作的参数定义要求。
    """
    mid = str(browser_info.auth_info.mid)
    await action_registry.create_action_for_user(request.action_id, mid)
    metadata = action_registry.get_action_metadata(request.action_id)

    if not metadata:
        return error_response(f"未找到操作: {request.action_id}")

    missing_params = []
    invalid_params = []
    errors = []

    for param in metadata.parameters:
        if param.required and param.name not in request.params:
            missing_params.append(param.name)
        elif param.name in request.params:
            value = request.params[param.name]
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
                    pass

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


@router.post(BrowserControlRouterPath.actions_execute_step, summary="单步执行操作")
async def execute_action_step(
    request: ExecuteStepRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ExecuteStepResponse]:
    """单步执行操作
    
    用于复合操作的逐步执行或调试。
    - 如果 action_id 是复合操作，执行指定 step_index 的步骤
    - 如果 action_id 是普通操作，执行该操作
    """
    try:
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
