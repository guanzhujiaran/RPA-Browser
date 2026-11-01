"""MCP 调试路由（tag = ``MCP 调试``）。

这些接口唯一消费者是 MCP 工具：``fastapi-mcp`` 按 OpenAPI 自动生成工具，
因此这里显式指定 ``operation_id``（工具名）与 ``summary``（工具描述），
保证 AI 侧看到的工具名稳定、可读。

约定：
- 不修改任何既有路由的 ``operation_id``（避免前端 SDK 生成结果变化）；
- 鉴权与既有接口一致：``get_auth_info_from_header`` / ``verify_browser_ownership``；
- 服务层异常统一转业务错误码返回，不抛 500。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from bili_common.models.depends import BrowserReqAuthInfo
from bili_common.models.response import (
    StandardResponse,
    error_response,
    success_response,
)
from bili_common.models.response_code import ResponseCode

from app.mcp.models import (
    DebugActionDetail,
    DebugActionListData,
    DebugExecuteData,
    DebugRunData,
    DebugSaveData,
    RunLogReport,
    ValidationReport,
)
from app.mcp.requests import (
    ExecuteDebugRequest,
    LoosePayload,
    GetActionRequest,
    ListActionsRequest,
    RecentActionLogsData,
    RecentActionLogsRequest,
    RunLogsRequest,
    RunWorkflowRequest,
    SaveActionRequest,
    ValidateStepsRequest,
)
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.execution.debug_service import DebugServiceError, debug_service
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.security_depends import _verify_browser_ownership_core
from app.utils.depends.session_manager import DatabaseSessionManager
from sqlmodel.ext.asyncio.session import AsyncSession
from ..base import new_mcp_debug_router

router = new_mcp_debug_router()


async def verify_debug_browser(
    auth_info: Annotated[AuthInfo, Depends(get_auth_info_from_header)],
    session: Annotated[AsyncSession, Depends(DatabaseSessionManager.get_db_session)],
    browser_id: str = Query(
        ...,
        description=(
            "目标浏览器 id（雪花 id 的十进制字符串）。"
            "必须是当前用户自己的浏览器，否则报「浏览器不属于该用户」。"
        ),
    ),
) -> BrowserReqAuthInfo:
    """调试路由专用的浏览器归属校验。

    与 ``verify_browser_ownership`` 逻辑一致，但 ``browser_id`` 显式声明为 **str**：
    MCP 工具 schema 里 ``int | str`` 会被退化成 integer，
    客户端传字符串（雪花 id 的推荐传法）就会在参数校验阶段被拒。
    """
    return await _verify_browser_ownership_core(browser_id, auth_info, session)


def _steps_json(steps: list[LoosePayload] | None) -> list[object] | None:
    """请求模型里的步骤对象转普通 JSON（服务层只认 JSON 结构）。"""
    if steps is None:
        return None
    return [step.to_json() for step in steps]


def _build_auth_headers(auth_info: AuthInfo) -> dict[str, str]:
    """构造供 HTTP 请求类动作透传的认证头（与执行路由一致）。"""
    return {
        "x-bili-mid": str(auth_info.mid),
        "x-bili-level": f"level{auth_info.level}",
    }


# ============ 读：动作 / 日志 ============


@router.post(
    BrowserControlRouterPath.debug_actions_list,
    operation_id="rpa_debug_actions_list",
    summary="列出当前用户的自定义动作",
)
async def list_debug_actions(
    request: ListActionsRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[DebugActionListData]:
    """按名称模糊搜索动作，返回 action_id / name / steps_count / is_enabled / is_public / updated_at。

    调试任何动作的第一步：先拿到 action_id（复合动作为雪花 int）。
    """
    data = await debug_service.list_actions(
        auth.mid,
        keyword=request.keyword,
        page=request.page,
        per_page=request.per_page,
    )
    return success_response(data)


@router.post(
    BrowserControlRouterPath.debug_actions_get,
    operation_id="rpa_debug_action_get",
    summary="导出动作全量定义（含 steps）",
)
async def get_debug_action(
    request: GetActionRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[DebugActionDetail]:
    """导出动作的 DB 当前真值（steps 可直接修改后再落库）。"""
    try:
        data = await debug_service.get_action(auth.mid, request.action_id)
    except DebugServiceError as exc:
        return error_response(ResponseCode.BUSINESS_ERROR, str(exc))
    return success_response(data)


@router.post(
    BrowserControlRouterPath.debug_action_logs_recent,
    operation_id="rpa_debug_action_logs",
    summary="查动作最近的执行日志",
)
async def recent_action_logs(
    request: RecentActionLogsRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[RecentActionLogsData]:
    """查某动作最近 N 条执行日志；返回的 execution_id 可喂给 rpa_debug_run_logs。"""
    data = await debug_service.recent_action_logs(
        auth.mid, action_id=request.action_id, limit=request.limit
    )
    return success_response(data)


# ============ 校验 ============


@router.post(
    BrowserControlRouterPath.debug_actions_validate,
    operation_id="rpa_debug_steps_validate",
    summary="静态校验步骤（不联网、不开浏览器）",
)
async def validate_debug_steps(
    request: ValidateStepsRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[ValidationReport]:
    """校验 steps 数组或已保存动作：结构反序列化 + Pipeline 编译。

    返回的 issues 带 ``steps[i].children[j].params.xxx`` 精确定位路径，
    按提示改 JSON 后再次调用即可，没有任何浏览器副作用。
    """
    try:
        report = await debug_service.validate(
            auth.mid, steps=_steps_json(request.steps), action_id=request.action_id
        )
    except DebugServiceError as exc:
        return error_response(ResponseCode.BUSINESS_ERROR, str(exc))
    return success_response(report)


# ============ 执行 ============


@router.post(
    BrowserControlRouterPath.debug_actions_execute,
    operation_id="rpa_debug_action_execute",
    summary="执行动作或内联步骤（默认先静态校验）",
)
async def execute_debug_action(
    request: ExecuteDebugRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_debug_browser),
) -> StandardResponse[DebugExecuteData]:
    """同步执行已保存动作或未落库的内联步骤。

    - ``action_id``：执行 DB 中的动作（复合动作会先校验）；
    - ``steps``：直接试跑内联步骤（改完先验，不必先写库）；
    - 校验不通过时返回 ``blocked_by_validation=true``，不发执行请求（``force=true`` 跳过）。
    """
    try:
        data = await debug_service.execute(
            browser_info.auth_info.mid,
            browser_id=browser_info.browser_id,
            auth_headers=_build_auth_headers(browser_info.auth_info),
            action_id=request.action_id,
            steps=_steps_json(request.steps),
            params=request.params.to_json() if request.params is not None else None,
            variables=request.variables.to_json() if request.variables is not None else None,
            output_vars=request.output_vars,
            page_index=request.page_index,
            force=request.force,
        )
    except DebugServiceError as exc:
        return error_response(ResponseCode.BUSINESS_ERROR, str(exc))
    return success_response(data)


# ============ 落库 ============


@router.post(
    BrowserControlRouterPath.debug_actions_save,
    operation_id="rpa_debug_action_save",
    summary="落库动作定义（同名 upsert）并回读校验",
)
async def save_debug_action(
    request: SaveActionRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[DebugSaveData]:
    """把动作 JSON 内容写入数据库（更新已有 / 新建私有动作），并回读比对 steps。

    - ``action_id`` 给出则更新该动作；否则按 ``name`` 精确匹配，匹配不到则新建；
    - 静态校验不通过或 steps 为空时拒绝写库；
    - ``dry_run=true`` 只返回将执行的模式与摘要。
    """
    try:
        data = await debug_service.save(
            auth.mid,
            content=request.content.to_json(),
            action_id=request.action_id,
            dry_run=request.dry_run,
        )
    except DebugServiceError as exc:
        return error_response(ResponseCode.BUSINESS_ERROR, str(exc))
    return success_response(data)


# ============ 工作流 ============


@router.post(
    BrowserControlRouterPath.debug_workflows_run_and_wait,
    operation_id="rpa_debug_workflow_run",
    summary="运行工作流并等待终态",
)
async def run_debug_workflow(
    request: RunWorkflowRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_debug_browser),
) -> StandardResponse[DebugRunData]:
    """启动一次工作流运行，服务端轮询运行记录直到终态或超时。

    返回 ``execution_id`` 后即可用 ``rpa_debug_run_logs`` 看步骤明细。
    """
    try:
        data = await debug_service.run_workflow_and_wait(
            browser_info.auth_info.mid,
            workflow_id=request.workflow_id,
            browser_id=browser_info.browser_id,
            auth_headers=_build_auth_headers(browser_info.auth_info),
            timeout_seconds=request.timeout_seconds,
        )
    except DebugServiceError as exc:
        return error_response(ResponseCode.BUSINESS_ERROR, str(exc))
    return success_response(data)


@router.post(
    BrowserControlRouterPath.debug_runs_logs,
    operation_id="rpa_debug_run_logs",
    summary="按运行/执行批次查步骤链路并定位失败步骤",
)
async def get_debug_run_logs(
    request: RunLogsRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[RunLogReport]:
    """拉取一次运行 / 执行的步骤链路。

    返回 ``tree``（按 depth 缩进的调用链）、``failed``（失败项含 params / logs 摘要）
    与 ``recovery_hints``（候选 ``steps[i]`` 路径，``ambiguous=true`` 表示需人工定位）。
    """
    try:
        report = await debug_service.get_run_logs(
            auth.mid,
            run_id=request.run_id,
            execution_id=request.execution_id,
            max_items=request.max_items,
            only_failed=request.only_failed,
            detail_limit=request.detail_limit,
        )
    except DebugServiceError as exc:
        return error_response(ResponseCode.BUSINESS_ERROR, str(exc))
    return success_response(report)


__all__ = ["router"]
