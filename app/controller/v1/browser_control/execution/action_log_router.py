"""
浏览器操作日志路由

提供日志查询 API：
    - 列表（筛选 + 分页）/ 详情 / 按执行批次查链路 / 删除 / 清空 / 统计

（采集「是否启用 / 采集哪些字段」已直接落到 action 的基础配置上，无独立配置接口。）
"""
from typing import List

from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from fastapi import Depends
from app.services.execution.crud_service import (
    action_log_crud_svr,
)
from app.models.log.models import (
    ActionLogListRequest,
    ActionLogItemResponse,
    ActionLogDetailResponse,
    ActionLogByExecutionRequest,
    ActionLogDeleteRequest,
    ActionLogClearRequest,
    ActionLogStatsResponse,
)
from app.models.base.base_sqlmodel import BasePaginationResp
from ..base import new_action_log_router

router = new_action_log_router()


# ============ 日志记录查询 ============


@router.post(BrowserControlRouterPath.action_logs_list, summary="查询操作日志列表")
async def list_action_logs(
    request: ActionLogListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[ActionLogItemResponse]]:
    """按筛选条件查询操作日志（分页）

    支持 action_id / execution_id / workflow_id / browser_id / source / status /
    success / 关键字 / 时间范围筛选。
    """
    skip = (request.page - 1) * request.per_page
    total = await action_log_crud_svr.count(
        auth.mid,
        action_id=request.action_id,
        execution_id=request.execution_id,
        workflow_id=request.workflow_id,
        browser_id=request.browser_id,
        source=request.source,
        status=request.status,
        success=request.success,
        keyword=request.keyword,
        started_after=request.started_after,
        started_before=request.started_before,
    )
    rows = await action_log_crud_svr.list(
        auth.mid,
        skip=skip,
        limit=request.per_page,
        order_desc=request.order_desc,
        action_id=request.action_id,
        execution_id=request.execution_id,
        workflow_id=request.workflow_id,
        browser_id=request.browser_id,
        source=request.source,
        status=request.status,
        success=request.success,
        keyword=request.keyword,
        started_after=request.started_after,
        started_before=request.started_before,
    )
    items = [_to_item(r) for r in rows]
    return success_response(
        BasePaginationResp[ActionLogItemResponse](
            page=request.page,
            per_page=request.per_page,
            total=total,
            items=items,
        )
    )


@router.post(BrowserControlRouterPath.action_logs_get, summary="获取单条日志详情")
async def get_action_log(
    request: dict,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[ActionLogDetailResponse]:
    """按 log_id 获取单条日志详情"""
    log_id = request.get("log_id")
    if not log_id:
        return error_response(400, "缺少 log_id")
    model = await action_log_crud_svr.get_by_log_id(auth.mid, log_id)
    if not model:
        return error_response(404, "日志不存在")
    return success_response(_to_detail(model))


@router.post(BrowserControlRouterPath.action_logs_by_execution, summary="按执行批次查询完整链路")
async def list_action_logs_by_execution(
    request: ActionLogByExecutionRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[ActionLogItemResponse]]:
    """按 execution_id 拉取一次执行的完整链路（按写入顺序，便于还原调用树）"""
    if not request.execution_id:
        return error_response(400, "缺少 execution_id")
    rows = await action_log_crud_svr.list_by_execution(auth.mid, request.execution_id)
    return success_response([_to_item(r) for r in rows])


@router.post(BrowserControlRouterPath.action_logs_delete, summary="批量删除日志")
async def delete_action_logs(
    request: ActionLogDeleteRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """按 log_id 列表批量删除日志"""
    if not request.log_ids:
        return error_response(400, "缺少 log_ids")
    deleted = await action_log_crud_svr.delete_by_log_ids(auth.mid, request.log_ids)
    return success_response({"deleted": deleted})


@router.post(BrowserControlRouterPath.action_logs_clear, summary="按条件清空日志")
async def clear_action_logs(
    request: ActionLogClearRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """按筛选条件批量清空日志"""
    deleted = await action_log_crud_svr.clear(
        auth.mid,
        action_id=request.action_id,
        execution_id=request.execution_id,
        workflow_id=request.workflow_id,
        browser_id=request.browser_id,
        source=request.source,
        status=request.status,
        success=request.success,
        keyword=request.keyword,
        started_after=request.started_after,
        started_before=request.started_before,
    )
    return success_response({"deleted": deleted})


@router.post(BrowserControlRouterPath.action_logs_stats, summary="操作日志统计")
async def stats_action_logs(
    request: dict | None = None,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[ActionLogStatsResponse]:
    """按 action 聚合统计最近 N 天的执行情况（默认 7 天）"""
    days = 7
    if request and isinstance(request, dict):
        days = int(request.get("days", 7) or 7)
    data = await action_log_crud_svr.stats(auth.mid, days)
    return success_response(ActionLogStatsResponse(**data))


# ============ 序列化工具 ============


def _to_item(model) -> ActionLogItemResponse:
    return ActionLogItemResponse(
        id=model.id,
        log_id=model.log_id,
        mid=model.mid,
        execution_id=model.execution_id,
        parent_execution_id=model.parent_execution_id,
        depth=model.depth,
        action_id=model.action_id,
        action_name=model.action_name,
        action_type=model.action_type,
        source=model.source,
        workflow_id=model.workflow_id,
        browser_id=model.browser_id,
        session_id=model.session_id,
        page_url=model.page_url,
        status=model.status,
        success=model.success,
        params=model.params,
        result_data=model.result_data,
        variables=model.variables,
        logs=model.logs or [],
        error_message=model.error_message,
        execution_time=model.execution_time,
        started_at=model.started_at,
        finished_at=model.finished_at,
    )


def _to_detail(model) -> ActionLogDetailResponse:
    return ActionLogDetailResponse(**_to_item(model).model_dump())


__all__ = ["router"]
