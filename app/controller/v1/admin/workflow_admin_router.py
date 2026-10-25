"""工作流管理端接口（仅 root）

root 对**全部用户**的工作流拥有管理特权：跨作者查看、编辑、启停、删除、
查询运行记录。与用户侧 ``/browser/control/workflows/*``（按 mid 归属隔离）完全分离，
root 不再与普通用户共用一个工作流页面（见用户诉求：root 需要单独的管理界面）。

与用户侧的关键差异（root 特权）：
- 列表/详情**不按 mid 归属过滤**，返回全部用户的工作流（列表附带作者 mid）；
- 变更操作**跳过审批单强制**（``assert_approved``）——root 本身即审批方。

注意：root **不代为运行**他人工作流（执行归作者，root 只做监督与治理），
故这里不提供「立即运行」端点。

安全边界：所有端点均以 ``require_root`` 守卫，非 root 一律 403。
"""

from fastapi import APIRouter, Depends

from bili_common.deps.auth import AuthInfo
from bili_common.models.response import (
    StandardResponse,
    error_response,
    success_response,
)
from bili_common.models.response_code import ResponseCode

from app.models.base.base_sqlmodel import BasePaginationResp
from app.models.router.router_prefix import AdminWorkflowRouterPath
from app.models.workflow.models import (
    WorkflowAdminListRequest,
    WorkflowAdminListItemResponse,
    WorkflowAdminSetEnabledRequest,
    WorkflowDetailResponse,
    WorkflowRunLogItemResponse,
    WorkflowRunLogListRequest,
    WorkflowUpdateRequest,
)
from app.services.admin_audit import log_admin_action
from app.services.execution.crud_service import (
    workflow_crud_svr,
    workflow_run_crud_svr,
)
from app.services.execution.workflow_scheduler import (
    remove_workflow_job,
    sync_workflow_job,
    validate_cron,
)
from app.utils.depends.admin_depends import require_root

router = APIRouter()  # 由 admin/__init__.py 聚合父路由统一提供前缀 /api/admin/rpa


def _validate_trigger(
    trigger_type: str, trigger_config: dict | None, browser_id: int | None
) -> str | None:
    """校验触发配置合法性；返回错误信息（None 表示通过）"""
    if trigger_type not in ("manual", "cron"):
        return f"不支持的触发类型: {trigger_type}（仅支持 manual/cron）"
    if trigger_type == "cron":
        cron = (trigger_config or {}).get("cron")
        if not isinstance(cron, str) or not cron.strip():
            return "定时触发需要提供 trigger_config.cron 表达式"
        if not validate_cron(cron.strip()):
            return f"cron 表达式非法（需 5 段，如 */5 * * * *）: {cron}"
        if browser_id is None:
            return "定时触发必须指定执行目标浏览器"
    return None


def _to_detail(model) -> WorkflowDetailResponse:
    """工作流 ORM 模型 → 详情响应（与用户侧字段口径一致）"""
    return WorkflowDetailResponse(
        id=model.id,
        workflow_id=model.workflow_id,
        name=model.name,
        custom_action_id=model.custom_action_id,
        description=model.description,
        browser_id=model.browser_id,
        trigger_type=model.trigger_type,
        trigger_config=model.trigger_config or {},
        is_enabled=model.is_enabled,
        is_public=model.is_public,
        likes_count=model.likes_count,
        reports_count=model.reports_count,
        is_verified=model.is_verified,
        forks_count=model.forks_count,
        forked_from_id=model.forked_from_id,
        last_run_at=model.last_run_at,
        last_run_status=model.last_run_status,
        next_run_at=model.next_run_at,
        notify_on_start=model.notify_on_start,
        notify_on_end=model.notify_on_end,
        log_enabled=model.log_enabled,
        log_debug=model.log_debug,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_list_item(model) -> WorkflowAdminListItemResponse:
    """工作流 ORM 模型 → 管理端列表项（附带作者 mid）"""
    return WorkflowAdminListItemResponse(
        id=model.id,
        workflow_id=model.workflow_id,
        name=model.name,
        custom_action_id=model.custom_action_id,
        description=model.description,
        browser_id=model.browser_id,
        trigger_type=model.trigger_type,
        trigger_config=model.trigger_config or {},
        is_enabled=model.is_enabled,
        is_public=model.is_public,
        likes_count=model.likes_count,
        reports_count=model.reports_count,
        is_verified=model.is_verified,
        forks_count=model.forks_count,
        forked_from_id=model.forked_from_id,
        last_run_at=model.last_run_at,
        last_run_status=model.last_run_status,
        next_run_at=model.next_run_at,
        notify_on_start=model.notify_on_start,
        notify_on_end=model.notify_on_end,
        log_enabled=model.log_enabled,
        log_debug=model.log_debug,
        created_at=model.created_at,
        updated_at=model.updated_at,
        mid=model.mid,
    )


@router.post(
    AdminWorkflowRouterPath.workflow_list,
    response_model=StandardResponse[
        BasePaginationResp[WorkflowAdminListItemResponse]
    ],
    summary="获取全量工作流列表（管理端）",
)
async def list_workflows_admin(
    request: WorkflowAdminListRequest,
    auth: AuthInfo = Depends(require_root),
) -> StandardResponse[BasePaginationResp[WorkflowAdminListItemResponse]]:
    """跨作者列出全部用户的工作流（仅 root），支持名称/作者/公开/启停筛选与排序"""
    skip = (request.page - 1) * request.per_page
    total = await workflow_crud_svr.count_all(
        mid=request.mid,
        name=request.name,
        is_public=request.is_public,
        is_enabled=request.is_enabled,
    )
    models = await workflow_crud_svr.list_all(
        skip=skip,
        limit=request.per_page,
        sort_by=request.sort_by.value,
        sort_order=request.sort_order.value,
        mid=request.mid,
        name=request.name,
        is_public=request.is_public,
        is_enabled=request.is_enabled,
    )
    return success_response(
        BasePaginationResp[WorkflowAdminListItemResponse](
            page=request.page,
            per_page=request.per_page,
            total=total,
            items=[_to_list_item(m) for m in models],
        )
    )


@router.post(
    AdminWorkflowRouterPath.workflow_get,
    response_model=StandardResponse[WorkflowDetailResponse],
    summary="获取任意工作流详情（管理端）",
)
async def get_workflow_admin(
    request: dict,
    auth: AuthInfo = Depends(require_root),
) -> StandardResponse[WorkflowDetailResponse]:
    """按 id 获取任意用户的工作流详情（仅 root，不做归属校验）

    Args:
        request: {"id": <工作流ID>}
    """
    workflow_id = request.get("id")
    if not workflow_id:
        return error_response(400, "缺少工作流ID")
    model = await workflow_crud_svr.get_by_id(workflow_id)
    if not model:
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")
    return success_response(_to_detail(model))


@router.post(
    AdminWorkflowRouterPath.workflow_update,
    response_model=StandardResponse[WorkflowDetailResponse],
    summary="更新任意工作流（管理端）",
)
async def update_workflow_admin(
    request: WorkflowUpdateRequest,
    auth: AuthInfo = Depends(require_root),
) -> StandardResponse[WorkflowDetailResponse]:
    """更新任意用户的工作流（仅 root）

    与管理端特权一致：**跳过 publish / execute 审批单强制**，直接落库并同步定时任务。
    """
    existing = await workflow_crud_svr.get_by_id(request.id)
    if not existing:
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    # 触发配置校验：按「合并后」的最终值校验（未传的字段沿用原值）
    final_trigger_type = (
        request.trigger_type
        if request.trigger_type is not None
        else existing.trigger_type
    )
    final_trigger_config = (
        request.trigger_config
        if request.trigger_config is not None
        else (existing.trigger_config or {})
    )
    final_browser_id = (
        request.browser_id if request.browser_id is not None else existing.browser_id
    )
    trigger_error = _validate_trigger(
        final_trigger_type, final_trigger_config, final_browser_id
    )
    if trigger_error:
        return error_response(ResponseCode.INVALID_PARAM, trigger_error)

    model = await workflow_crud_svr.update(
        id=request.id,
        name=request.name,
        description=request.description,
        custom_action_id=request.custom_action_id,
        browser_id=request.browser_id,
        trigger_type=request.trigger_type,
        trigger_config=request.trigger_config,
        is_enabled=request.is_enabled,
        is_public=request.is_public,
        notify_on_start=request.notify_on_start,
        notify_on_end=request.notify_on_end,
        log_enabled=request.log_enabled,
        log_debug=request.log_debug,
    )
    if not model:
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    await sync_workflow_job(model)
    model = await workflow_crud_svr.get_by_id(model.id) or model

    await log_admin_action(
        auth.mid,
        "workflow:update",
        "workflow",
        request.id,
        f"owner={existing.mid}, name={model.name}, enabled={model.is_enabled}",
    )
    return success_response(_to_detail(model), msg="工作流已更新")


@router.post(
    AdminWorkflowRouterPath.workflow_set_enabled,
    response_model=StandardResponse[WorkflowDetailResponse],
    summary="启用 / 停用任意工作流（管理端）",
)
async def set_workflow_enabled_admin(
    request: WorkflowAdminSetEnabledRequest,
    auth: AuthInfo = Depends(require_root),
) -> StandardResponse[WorkflowDetailResponse]:
    """启用或停用任意用户的工作流（仅 root），并同步定时调度任务"""
    model = await workflow_crud_svr.get_by_id(request.id)
    if not model:
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    updated = await workflow_crud_svr.update(
        id=request.id, is_enabled=request.is_enabled
    )
    if not updated:
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    await sync_workflow_job(updated)
    updated = await workflow_crud_svr.get_by_id(updated.id) or updated

    await log_admin_action(
        auth.mid,
        "workflow:set-enabled",
        "workflow",
        request.id,
        f"owner={model.mid}, is_enabled={request.is_enabled}",
    )
    return success_response(
        _to_detail(updated),
        msg="工作流已启用" if request.is_enabled else "工作流已停用",
    )


@router.post(
    AdminWorkflowRouterPath.workflow_delete,
    response_model=StandardResponse[dict],
    summary="删除任意工作流（管理端）",
)
async def delete_workflow_admin(
    request: dict,
    auth: AuthInfo = Depends(require_root),
) -> StandardResponse[dict]:
    """删除任意用户的工作流（仅 root）

    Args:
        request: {"id": <工作流ID>}
    """
    workflow_id = request.get("id")
    if not workflow_id:
        return error_response(400, "缺少工作流ID")

    model = await workflow_crud_svr.get_by_id(workflow_id)
    if not model:
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    # 先摘掉定时任务，避免删除后仍被调度
    remove_workflow_job(model.workflow_id)

    if not await workflow_crud_svr.delete(workflow_id):
        return error_response(500, "删除失败")

    await log_admin_action(
        auth.mid,
        "workflow:delete",
        "workflow",
        workflow_id,
        f"owner={model.mid}, name={model.name}",
    )
    return success_response({"message": "删除成功"})


@router.post(
    AdminWorkflowRouterPath.workflow_runs,
    response_model=StandardResponse[
        BasePaginationResp[WorkflowRunLogItemResponse]
    ],
    summary="查询任意工作流的运行记录（管理端）",
)
async def list_workflow_runs_admin(
    request: WorkflowRunLogListRequest,
    auth: AuthInfo = Depends(require_root),
) -> StandardResponse[BasePaginationResp[WorkflowRunLogItemResponse]]:
    """分页查询任意工作流的运行记录（仅 root，不做归属校验，最新在前）"""
    model = await workflow_crud_svr.get_by_workflow_id(request.workflow_id)
    if not model:
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    skip = (request.page - 1) * request.per_page
    total = await workflow_run_crud_svr.count_by_workflow(request.workflow_id)
    rows = await workflow_run_crud_svr.list_by_workflow(
        request.workflow_id, skip=skip, limit=request.per_page
    )
    items = [
        WorkflowRunLogItemResponse(**(await workflow_run_crud_svr.to_dict(row)))
        for row in rows
    ]
    return success_response(
        BasePaginationResp[WorkflowRunLogItemResponse](
            page=request.page,
            per_page=request.per_page,
            total=total,
            items=items,
        )
    )


__all__ = ["router"]
