"""
执行引擎路由

提供操作执行相关的 API（执行、批量执行、调试等）
自定义操作和工作流的 CRUD 已迁移到 action_router.py 和 workflow_router.py
"""
from fastapi import Depends
from app.models.response import StandardResponse, success_response, error_response
from bili_common.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.security_depends import verify_browser_ownership
from bili_common.models.depends import BrowserReqAuthInfo
from app.services.RPA_browser.session.live_service import live_service
from app.services.execution.engine import ExecutionEngine
from app.services.execution.action_registry import action_registry
from app.services.execution.actions.control_flow import CompositeAction as CompositeActionClass
from app.models.execution.action_params import BaseWorkflowStep
from app.models.execution.request_params import (
    ActionExecutionRequest,
    WorkflowExecutionRequest,
    params_to_dict,
)
from app.models.workflow.models import (
    ActionExecuteRequest,
    ActionResultResponse,
    WorkflowExecuteRequest,
    WorkflowExecuteResponse,
    ActionPreviewRequest,
    ActionPreviewResponse,
    StepPreviewItem,
    ActionValidateRequest,
    ActionValidateResponse,
    ExecuteStepRequest,
    ExecuteStepResponse,
)
from app.services.execution.crud_service import workflow_crud_svr
from app.models.execution.system_services import build_method_responses, RpcMethodInfoResponse
from ..base import new_execution_router

router = new_execution_router()
# 全局单例 — 引擎自身无状态，路由层持有即可
execution_engine = ExecutionEngine()


async def _resolve_page(mid: int, browser_id: int | str, page_index: int | None = None):
    """从 LiveService 解析浏览器页面，供路由层传入引擎"""
    entry = live_service.get_browser_session_entry(mid=mid, browser_id=browser_id)
    if not entry:
        raise ValueError("浏览器不存在或未运行")

    if page_index is None:
        return await entry.browser_session.get_current_page()

    all_pages = entry.browser_session.all_pages
    if not all_pages:
        raise ValueError(f"浏览器 {browser_id} 没有打开任何页面")
    if not (0 <= page_index < len(all_pages)):
        raise ValueError(f"页面索引 {page_index} 超出范围 (0-{len(all_pages)-1})")
    return all_pages[page_index]


def _build_steps(step_reqs):
    """将 API 请求的步骤转换为 BaseWorkflowStep 列表"""
    steps = []
    for step_req in step_reqs:
        children = None
        if hasattr(step_req, 'children') and step_req.children:
            children = _build_steps(step_req.children)
        step = BaseWorkflowStep(
            action_id=step_req.action_id,
            params=step_req.params,
            retry=step_req.retry,
            loop_count=step_req.loop_count,
            loop_while=step_req.loop_while,
            loop_until=step_req.loop_until,
            condition=step_req.condition,
            children=children,
            input_vars=step_req.input_vars if hasattr(step_req, 'input_vars') else None,
            output_vars=step_req.output_vars if hasattr(step_req, 'output_vars') else None,
        )
        steps.append(step)
    return steps


def _build_auth_headers(auth_info) -> dict[str, str]:
    """从认证信息构建本系统认证请求头，供 HTTP 请求类操作按需透传"""
    return {
        "x-bili-mid": str(auth_info.mid),
        "x-bili-level": f"level{auth_info.level}",
    }


# ============ 操作执行 API ============


@router.post(BrowserControlRouterPath.actions_execute, summary="执行单个操作")
async def execute_action(
    request: ActionExecuteRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionResultResponse | None]:
    """执行单个操作"""
    merged_vars = {**request.variables, **request.input_vars} if request.input_vars else (request.variables or {})
    req = ActionExecutionRequest(
        mid=browser_info.auth_info.mid,
        browser_id=browser_info.browser_id,
        action_id=request.action_id,
        params=request.params,
        variables=merged_vars,
        input_data=merged_vars,
        output_vars=request.output_vars,
        page_index=request.page_index,
        auth_headers=_build_auth_headers(browser_info.auth_info),
    )
    page = await _resolve_page(browser_info.auth_info.mid, browser_info.browser_id, request.page_index)
    result = await execution_engine.execute_action(
        req,
        session_id=str(browser_info.browser_id),
        browser_id=str(browser_info.browser_id),
        page=page,
    )
    return success_response(
        ActionResultResponse(
            success=result.success,
            data=result.data,
            error=result.error,
            execution_time=result.execution_time,
            action_id=result.action_id,
            action_name=result.action_name,
            variables=result.variables,
            replaced_params=result.replaced_params,
        )
    )


# ============ 工作流执行 API ============


@router.post(BrowserControlRouterPath.workflows_execute, summary="执行工作流")
async def execute_workflow(
    request: WorkflowExecuteRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[WorkflowExecuteResponse]:
    """执行工作流

    支持两种模式：
    - 提供 action_id：执行已保存的自定义操作
    - 提供 steps：执行内联步骤（无需保存）
    """
    mid = browser_info.auth_info.mid
    bid= browser_info.browser_id
    req = WorkflowExecutionRequest(
        mid=mid,
        browser_id=bid,
        action_id=request.action_id or "",
        workflow_id=request.workflow_id,
        variables=request.variables or {},
        input_data=request.input_data,
        output_vars=request.output_vars,
        page_index=request.page_index,
        auth_headers=_build_auth_headers(browser_info.auth_info),
    )

    page = await _resolve_page(mid, bid, request.page_index)

    if request.steps:
        # PipelineBuilder 通过 getattr 统一访问步骤字段，无需 create_workflow_step 二次规范化
        plugins = await workflow_crud_svr.get_enabled_plugins(request.workflow_id) if request.workflow_id else []
        steps = _build_steps(request.steps)
        results = await execution_engine.execute_steps(
            req,
            steps=steps,
            session_id=str(bid),
            browser_id=str(bid),
            page=page,
            plugins=plugins,
        )
    elif request.action_id:
        from app.services.execution.crud_service import action_crud_svr, workflow_crud_svr
        from app.models.execution.action_params import _ensure_action_type, workflow_step_adapter
        action_model = await action_crud_svr.get_by_action_id(request.action_id)
        if not action_model:
            return error_response(ResponseCode.BUSINESS_ERROR, f"未找到操作: {request.action_id}")

        plugins = await workflow_crud_svr.get_enabled_plugins(request.workflow_id) if request.workflow_id else []

        normalized_steps = []
        for s in action_model.steps:
            if isinstance(s, dict):
                s = workflow_step_adapter.validate_python(_ensure_action_type(s))
            normalized_steps.append(s)

        results = await execution_engine.execute_steps(
            req,
            steps=normalized_steps,
            session_id=str(bid),
            browser_id=str(bid),
            page=page,
            plugins=plugins,
        )
    else:
        return error_response(ResponseCode.BUSINESS_ERROR, "需要提供 action_id 或 steps")

    results_data = [
        {
            "success": r.success,
            "data": r.data,
            "error": r.error,
            "execution_time": r.execution_time,
            "action_id": r.action_id,
            "action_name": r.action_name,
            "variables": r.variables,
            "replaced_params": r.replaced_params,
        }
        for r in results
    ]

    return success_response(
        WorkflowExecuteResponse(
            execution_id=request.workflow_id or request.action_id or "inline",
            results=results_data,
            summary={
                "total": len(results),
                "success": len([r for r in results if r.success]),
                "failed": len([r for r in results if not r.success]),
            },
        )
    )


# ============ 调试相关 API ============


@router.post(BrowserControlRouterPath.actions_preview, summary="预览参数替换结果")
async def preview_action_params(
    request: ActionPreviewRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionPreviewResponse]:
    """预览参数替换结果"""
    try:
        result = await execution_engine.preview_action(
            mid=browser_info.auth_info.mid,
            action_id=request.action_id,
            params=request.params,
            input_vars=request.input_vars,
        )
    except ValueError as e:
        return error_response(ResponseCode.BUSINESS_ERROR, str(e))

    steps_preview = [
        StepPreviewItem(**s) for s in result["steps_preview"]
    ]

    return success_response(
        ActionPreviewResponse(
            action_id=result["action_id"],
            action_name=result["action_name"],
            is_composite=result["is_composite"],
            steps_preview=steps_preview,
            replaced_params=result["replaced_params"],
            found_params=result["found_params"],
            preview_result=result["preview_result"],
            preview_variables=result["preview_variables"],
        )
    )


@router.post(BrowserControlRouterPath.actions_validate, summary="验证操作参数")
async def validate_action_params(
    request: ActionValidateRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ActionValidateResponse]:
    """验证操作参数"""
    try:
        result = await execution_engine.validate_action(
            mid=browser_info.auth_info.mid,
            action_id=request.action_id,
            params=request.params,
        )
    except ValueError as e:
        return error_response(ResponseCode.BUSINESS_ERROR, str(e))

    return success_response(
        ActionValidateResponse(
            valid=result["valid"],
            action_id=result["action_id"],
            action_name=result["action_name"],
            missing_params=result["missing_params"],
            invalid_params=result["invalid_params"],
            errors=result["errors"],
        )
    )


# ============ 单步执行 API ============


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
        mid = browser_info.auth_info.mid
        bid = browser_info.browser_id
        page = await _resolve_page(mid, bid, request.page_index)
        auth_headers = _build_auth_headers(browser_info.auth_info)

        action_class = await action_registry.get_action_class_for_user(request.action_id)
        if not action_class:
            raise ValueError(f"未找到操作: {request.action_id}")
        metadata = action_registry.get_action_metadata(request.action_id)
        if not metadata:
            raise ValueError(f"未找到操作: {request.action_id}")

        req = ActionExecutionRequest(
            mid=mid,
            browser_id=bid,
            variables=request.variables,
            page_index=request.page_index,
            action_id=request.action_id,
            params=request.params,
            auth_headers=auth_headers,
        )

        if issubclass(action_class, CompositeActionClass):
            params_dict = params_to_dict(request.params)
            steps = params_dict.get("steps", [])
            if request.step_index < 0 or request.step_index >= len(steps):
                raise ValueError(f"步骤索引 {request.step_index} 超出范围 (0-{len(steps)-1})")

            step = steps[request.step_index]
            step_params = execution_engine._replace_params(
                step.get("params", {}), request.variables)

            step_req = ActionExecutionRequest(
                mid=mid,
                browser_id=bid,
                variables=request.variables,
                page_index=request.page_index,
                action_id=step["action_id"],
                params=step_params,
                auth_headers=auth_headers,
            )
            result = await execution_engine.execute_action(
                step_req,
                session_id=str(bid),
                browser_id=str(bid),
                page=page,
            )
            step_index, action_id, action_name = request.step_index, step["action_id"], metadata.name
        else:
            result = await execution_engine.execute_action(
                req,
                session_id=str(bid),
                browser_id=str(bid),
                page=page,
            )
            step_index, action_id, action_name = 0, request.action_id, metadata.name

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
                    variables=result.variables,
                    replaced_params=result.replaced_params,
                ),
            )
        )
    except ValueError as e:
        return error_response(ResponseCode.BUSINESS_ERROR, str(e))


# ============ 系统 RPC 方法查询 API ============


@router.get(
    BrowserControlRouterPath.system_services_list,
    summary="获取可用的系统 RPC 方法列表",
)
async def list_system_services() -> StandardResponse[list[RpcMethodInfoResponse]]:
    """获取获取外部数据 Action 可选的 RPC 业务方法列表

    前端在配置获取外部数据 Action 时，应从该接口获取可选方法列表作为可选项展示，
    不允许用户随意输入外部方法名。每个方法对应一个独立的 RabbitMQ routing_key，
    由 FastapiApp 侧 RPC 服务端处理。
    """
    return success_response(
        build_method_responses()
    )