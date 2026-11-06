"""
Workflow 管理路由

提供工作流（Workflow）的 CRUD 和执行 API
"""

from app.services.execution.engine import ExecutionEngine
from loguru import logger
from bili_common.models.response import (
    StandardResponse,
    success_response,
    error_response,
)
from bili_common.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.mid_depends import (
    get_auth_info_from_header,
    get_optional_auth_info_from_header,
    AuthInfo,
)
from app.utils.depends.admin_depends import assert_approved
from app.utils.depends.security_depends import verify_browser_ownership
from bili_common.models.depends import BrowserReqAuthInfo
from fastapi import Depends
from fastapi.responses import StreamingResponse

from app.config import settings
from app.services.RPA_browser.session.live_service import live_service
from app.models.common.exceptions.base_exception import (
    BaseException as CustomBaseException,
    BrowserWorkflowRunningException,
)
from app.services.execution.crud_service import (
    workflow_crud_svr,
    workflow_run_crud_svr,
    action_crud_svr,
)
from app.services.execution.workflow_scheduler import (
    validate_cron,
    sync_workflow_job,
    remove_workflow_job,
    compute_next_run,
    disable_workflow_schedule,
)
from app.services.execution.workflow_runner import (
    finalize_run_after_task,
    launch_workflow_run,
)
from app.services.execution.cancellation import cancellation_registry
from app.services.execution.run_tasks import workflow_run_tasks
from app.models.database.workflow.models import (
    WorkflowRunRecord,
    WorkflowRunTriggerEnum,
    WorkflowRunStatusEnum,
)
from app.models.workflow.models import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowListRequest,
    WorkflowDetailResponse,
    WorkflowListItemResponse,
    WorkflowCreateResponse,
    WorkflowDuplicateRequest,
    WorkflowDuplicateResponse,
    WorkflowForkRequest,
    WorkflowForkResponse,
    WorkflowIdRequest,
    WorkflowRunDetailRequest,
    WorkflowSyncRequest,
    WorkflowSyncResponse,
    WorkflowStepExecuteRequest,
    WorkflowStepExecuteResponse,
    WorkflowRunNowRequest,
    WorkflowRunStopRequest,
    WorkflowRunLogListRequest,
    WorkflowRunLogItemResponse,
    WorkflowRunLogDetailResponse,
    WorkflowRunStopResponse,
    WorkflowResourceRecordCreateRequest,
    WorkflowResourceRecordDeleteRequest,
    WorkflowResourceRecordDeleteResponse,
    WorkflowResourceRecordListRequest,
    WorkflowResourceRecordItemResponse,
    UpstreamSyncFlag,
)
from app.models.base.base_sqlmodel import BasePaginationReq, BasePaginationResp
from app.models.database.workflow.resource_record import ResourceProcessStatus
from app.models.runtime.workflow_progress import WorkflowRunProgressData
from app.models.workflow.models import WorkflowRunResumeRequest
from app.services.execution.crud_service.resource_record_crud import (
    ResourceRecordService,
)
from app.services.execution.workflow_progress import (
    get_run_reporter,
    iter_workflow_run_events,
)
from app.models.execution.request_params import (
    ActionExecutionRequest,
)
from ..base import new_workflow_router

execution_engine = ExecutionEngine()


def _build_auth_headers(auth_info) -> dict[str, str]:
    """从认证信息构建本系统认证请求头，供 HTTP 请求类操作按需透传"""
    return {
        "x-bili-mid": str(auth_info.mid),
        "x-bili-level": f"level{auth_info.level}",
    }


def _upstream_sync_kwargs(
    model, flag: UpstreamSyncFlag | None = None
) -> dict:
    """构造响应中的上游同步状态字段（docs/fork-上游同步计划书.md）

    Args:
        model: UserWorkflow
        flag: attach_upstream_update_flags 的批量计算结果；不传则按"无上游更新"处理
    """
    return {
        "has_upstream_update": flag.has_upstream_update if flag else False,
        "upstream_updated_at": flag.upstream_updated_at if flag else None,
        "last_synced_at": (
            int(model.last_synced_at.timestamp()) if model.last_synced_at else None
        ),
        "auto_sync_upstream": model.auto_sync_upstream,
        "local_steps_modified": model.local_steps_modified,
    }


def _validate_trigger(
    trigger_type: str, trigger_config: dict | None, browser_id: int | None
) -> str | None:
    """校验触发配置合法性；返回错误信息（None 表示通过）

    工作流是调度外壳：定时（cron）触发必须有可解析的 cron 表达式与稳定的执行目标浏览器。
    """
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


async def _resolve_page(mid: int, browser_id: int, page_index: int | None = None):
    """从 LiveService 解析浏览器页面

    会话不存在时由 ``get_browser_session_entry`` 抛 ``BrowserNotStartedException``（code=1007）
    表达，前端据此引导用户重新启动（本入口不承担启动职责，见计划书 §5.17）。
    工作流执行期间拒绝调试类调用（执行期互斥）；直播为只读拉流，不受影响。
    """
    entry = live_service.get_browser_session_entry(mid=mid, browser_id=browser_id)
    if entry.is_workflow_running:
        raise BrowserWorkflowRunningException()
    if page_index is not None:
        all_pages = entry.browser_session.all_pages
        if not all_pages:
            raise ValueError(f"浏览器 {browser_id} 没有打开任何页面")
        if not (0 <= page_index < len(all_pages)):
            raise ValueError(f"页面索引 {page_index} 超出范围 (0-{len(all_pages) - 1})")
        return all_pages[page_index]
    else:
        return await entry.browser_session.get_current_page()


router = new_workflow_router()


def _queue_position(run) -> int:
    """排队位置（多工作流并行后恒为 0，保留字段兼容前端）。"""
    return 0


# ============ 工作流管理（Workflow） ============


@router.post(BrowserControlRouterPath.workflows_create, summary="创建工作流")
async def create_workflow(
    request: WorkflowCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowCreateResponse]:
    """创建用户工作流

    工作流是「调度外壳」：引用一个已存在的自定义动作（多对一共享），
    并配置触发方式（manual/cron）与执行目标浏览器。步骤在动作侧调试，此处不编辑步骤。
    """
    # 触发配置校验（cron 需可解析且必须有目标浏览器）
    trigger_error = _validate_trigger(
        request.trigger_type, request.trigger_config, request.browser_id
    )
    if trigger_error:
        return error_response(ResponseCode.INVALID_PARAM, trigger_error)

    # 引用的动作必须存在（工作流不改写动作内容）
    if request.custom_action_id:
        action_model = await action_crud_svr.get_by_action_id(request.custom_action_id)
        if not action_model:
            return error_response(
                ResponseCode.BUSINESS_ERROR,
                f"引用的动作不存在: {request.custom_action_id}",
            )

    model = await workflow_crud_svr.create(
        name=request.name,
        mid=auth.mid,
        custom_action_id=request.custom_action_id,
        description=request.description,
        browser_id=request.browser_id,
        trigger_type=request.trigger_type,
        trigger_config=request.trigger_config,
        is_public=request.is_public,
        notify_inbox_on_start=request.notify_inbox_on_start,
        notify_inbox_on_end=request.notify_inbox_on_end,
        notify_push_on_start=request.notify_push_on_start,
        notify_push_on_end=request.notify_push_on_end,
        log_enabled=request.log_enabled,
        log_debug=request.log_debug,
        resume_on_restart=request.resume_on_restart,
        browser_ids=request.browser_ids,
        concurrent_exec=request.concurrent_exec,
        variables=request.variables,
    )

    # 同步定时任务（cron 生效即可被调度）
    await sync_workflow_job(model)

    return success_response(
        WorkflowCreateResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            message="工作流创建成功",
        )
    )


@router.post(BrowserControlRouterPath.workflows_import, summary="从 JSON 导入工作流")
async def import_workflow(
    request: WorkflowCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowCreateResponse]:
    """从 JSON 导入工作流

    请求体即「导出 JSON」内容（可携带 id / workflow_id / created_at / likes_count
    等数据库冗余字段，自动忽略）。导入会在**当前用户名下新建**一份工作流，
    workflow_id 重新生成；引用的自定义动作必须已存在。
    """
    return await create_workflow(request, auth)


@router.post(BrowserControlRouterPath.workflows_export, summary="导出工作流为 JSON")
async def export_workflow(
    request: WorkflowIdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowCreateRequest]:
    """导出工作流为 JSON

    返回体与「创建 / 导入」请求体字段完全一致，可直接保存为 .json 后再由导入接口还原。

    Args:
        request: {"id": <工作流数据库 ID>}
    """
    workflow_id = request.id
    model = await workflow_crud_svr.get_by_id(workflow_id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    return success_response(
        WorkflowCreateRequest(
            name=model.name,
            custom_action_id=model.custom_action_id,
            description=model.description,
            browser_id=model.browser_id,
            browser_ids=model.browser_ids,
            concurrent_exec=bool(model.concurrent_exec),
            trigger_type=model.trigger_type,
            trigger_config=model.trigger_config or {},
            is_public=model.is_public,
            notify_inbox_on_start=model.notify_inbox_on_start,
            notify_inbox_on_end=model.notify_inbox_on_end,
            notify_push_on_start=model.notify_push_on_start,
            notify_push_on_end=model.notify_push_on_end,
            log_enabled=model.log_enabled,
            log_debug=model.log_debug,
            resume_on_restart=model.resume_on_restart,
            variables=model.variables or {},
        )
    )


@router.post(BrowserControlRouterPath.workflows_list, summary="获取工作流列表")
async def list_workflows(
    request: WorkflowListRequest = None,
    auth: AuthInfo | None = Depends(get_optional_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[WorkflowListItemResponse]]:
    """获取工作流列表（可选登录）

    已登录：按 filter_type 返回「我的 / 公开 / 社区（他人公开）/ 认证 / 全部」；
    未登录（游客）：只返回公开内容，private 恒为空（社区广场匿名浏览）。
    """
    if request is None:
        request = WorkflowListRequest()

    # 计算 skip
    skip = (request.page - 1) * request.per_page

    mid = auth.mid if auth else None

    # 获取总数
    total = await workflow_crud_svr.count_by_user(
        mid=mid, filter_type=request.filter_type
    )

    # 获取列表数据
    models = await workflow_crud_svr.list_by_user(
        mid=mid,
        skip=skip,
        limit=request.per_page,
        filter_type=request.filter_type,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
    )

    # 当前进行中的运行：一次批量查询带出 workflow_id -> run_id，供卡片展示「运行中/停止」
    running_run_ids = await workflow_run_crud_svr.list_running_run_ids(
        [model.workflow_id for model in models]
    )

    # 批量计算上游同步状态（一次 IN 查询，避免 N+1）
    sync_flags = await workflow_crud_svr.attach_upstream_update_flags(models)

    items = []
    for model in models:
        items.append(
            WorkflowListItemResponse(
                id=model.id,
                workflow_id=model.workflow_id,
                name=model.name,
                custom_action_id=model.custom_action_id,
                description=model.description,
                browser_id=model.browser_id,
                browser_ids=model.browser_ids,
                concurrent_exec=bool(model.concurrent_exec),
                trigger_type=model.trigger_type,
                trigger_config=model.trigger_config or {},
                is_enabled=model.is_enabled,
                is_public=model.is_public,
                likes_count=model.likes_count,
                reports_count=model.reports_count,
                is_verified=model.is_verified,
                forks_count=model.forks_count,
                forked_from_id=model.forked_from_id,
                running_run_id=running_run_ids.get(model.workflow_id),
                last_run_at=model.last_run_at,
                last_run_status=model.last_run_status,
                next_run_at=model.next_run_at,
                notify_inbox_on_start=model.notify_inbox_on_start,
                notify_inbox_on_end=model.notify_inbox_on_end,
                notify_push_on_start=model.notify_push_on_start,
                notify_push_on_end=model.notify_push_on_end,
                notify_on_start=model.notify_on_start,
                notify_on_end=model.notify_on_end,
                log_enabled=model.log_enabled,
            log_debug=model.log_debug,
                resume_on_restart=model.resume_on_restart,
                created_at=model.created_at,
                updated_at=model.updated_at,
                **_upstream_sync_kwargs(model, sync_flags.get(model.id)),
            )
        )

    # 构建分页响应
    pagination = BasePaginationResp[WorkflowListItemResponse](
        page=request.page, per_page=request.per_page, total=total, items=items
    )

    return success_response(pagination)


@router.post(BrowserControlRouterPath.workflows_get, summary="获取工作流详情")
async def get_workflow_detail(
    request: WorkflowIdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDetailResponse]:
    """按工作流数据库 ID 获取详情（触发配置 / 关联动作 / 上游同步状态）。

    Args:
        request: {"id": <工作流数据库 ID>}；`workflow_id`（雪花）不是本接口主键
    """
    model = await workflow_crud_svr.get_by_id(request.id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    # 上游同步状态（单条详情）
    sync_flags = await workflow_crud_svr.attach_upstream_update_flags([model])

    # 获取工作流详情
    return success_response(
        WorkflowDetailResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            custom_action_id=model.custom_action_id,
            description=model.description,
            browser_id=model.browser_id,
            browser_ids=model.browser_ids,
            concurrent_exec=bool(model.concurrent_exec),
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
            notify_inbox_on_start=model.notify_inbox_on_start,
            notify_inbox_on_end=model.notify_inbox_on_end,
            notify_push_on_start=model.notify_push_on_start,
            notify_push_on_end=model.notify_push_on_end,
            notify_on_start=model.notify_on_start,
            notify_on_end=model.notify_on_end,
            log_enabled=model.log_enabled,
            log_debug=model.log_debug,
            variables=model.variables or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
            **_upstream_sync_kwargs(model, sync_flags.get(model.id)),
        )
    )


@router.post(BrowserControlRouterPath.workflows_update, summary="更新工作流")
async def update_workflow(
    request: WorkflowUpdateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDetailResponse]:
    """更新工作流"""
    existing = await workflow_crud_svr.get_by_id(request.id)
    if not existing or str(existing.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在或无权限")

    # publish 审批强制：把工作流公开到社区前，需已通过对应 publish 审批单（未通过保持 private）
    if request.is_public is True and not existing.is_public:
        await assert_approved("workflow", existing.workflow_id, "publish")

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

    # 变量语义互斥：整体替换 与 增量更新 不能同时传（见 docs/工作流变量增量更新计划书.md）
    if request.variables is not None and request.variables_patch:
        return error_response(
            ResponseCode.INVALID_PARAM,
            "variables 与 variables_patch 不能同时传（前者整体替换，后者增量更新）",
        )

    # 引用的动作必须存在（工作流不改写动作内容）
    if request.custom_action_id:
        action_model = await action_crud_svr.get_by_action_id(request.custom_action_id)
        if not action_model:
            return error_response(
                ResponseCode.BUSINESS_ERROR,
                f"引用的动作不存在: {request.custom_action_id}",
            )

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
        notify_inbox_on_start=request.notify_inbox_on_start,
        notify_inbox_on_end=request.notify_inbox_on_end,
        notify_push_on_start=request.notify_push_on_start,
        notify_push_on_end=request.notify_push_on_end,
        log_enabled=request.log_enabled,
        log_debug=request.log_debug,
        resume_on_restart=request.resume_on_restart,
        auto_sync_upstream=request.auto_sync_upstream,
        browser_ids=request.browser_ids,
        concurrent_exec=request.concurrent_exec,
        variables=request.variables,
        variables_patch=request.variables_patch,
    )

    if not model or str(model.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在或无权限")

    # 同步定时任务（启用/cron/浏览器任一变化都需重建或移除）
    await sync_workflow_job(model)
    model = await workflow_crud_svr.get_by_id(model.id) or model

    # 上游同步状态（更新后重新计算：本地修改标记可能刚被置上）
    sync_flags = await workflow_crud_svr.attach_upstream_update_flags([model])

    return success_response(
        WorkflowDetailResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            custom_action_id=model.custom_action_id,
            description=model.description,
            browser_id=model.browser_id,
            browser_ids=model.browser_ids,
            concurrent_exec=bool(model.concurrent_exec),
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
            notify_inbox_on_start=model.notify_inbox_on_start,
            notify_inbox_on_end=model.notify_inbox_on_end,
            notify_push_on_start=model.notify_push_on_start,
            notify_push_on_end=model.notify_push_on_end,
            notify_on_start=model.notify_on_start,
            notify_on_end=model.notify_on_end,
            log_enabled=model.log_enabled,
            log_debug=model.log_debug,
            variables=model.variables or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
            **_upstream_sync_kwargs(model, sync_flags.get(model.id)),
        )
    )


@router.post(BrowserControlRouterPath.workflows_delete, summary="删除工作流")
async def delete_workflow(
    request: WorkflowIdRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """删除工作流（同时摘掉它的定时任务，避免删除后仍被 cron 拉起）。

    Args:
        request: {"id": <工作流数据库 ID>}
    """
    workflow_id = request.id
    model = await workflow_crud_svr.get_by_id(workflow_id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在或无权限")

    # 先摘掉定时任务，避免删除后仍被调度
    remove_workflow_job(model.workflow_id)

    success = await workflow_crud_svr.delete(workflow_id)
    if success:
        return success_response({"message": "删除成功"})
    else:
        return error_response(500, "删除失败")


@router.post(BrowserControlRouterPath.workflows_duplicate, summary="复制工作流")
async def duplicate_workflow(
    request: WorkflowDuplicateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowDuplicateResponse]:
    """复制工作流（副本始终为私有，不继承公开状态）。

    Args:
        request: {"id": <原工作流数据库 ID>, "new_name": <副本名称，必填>}
    """
    workflow_id = request.id
    new_name = request.new_name

    # 获取原工作流
    original = await workflow_crud_svr.get_by_id(workflow_id)
    if not original or str(original.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在或无权限")

    # 创建副本（workflow_id 由 create 内部生成雪花 int）
    model = await workflow_crud_svr.create(
        name=new_name,
        mid=auth.mid,
        custom_action_id=original.custom_action_id,
        description=f"复制自: {original.name}",
        browser_id=original.browser_id,
        trigger_type=original.trigger_type,
        trigger_config=original.trigger_config,
        is_public=False,
    )

    # 副本若也是定时触发，同样需要注册调度任务
    await sync_workflow_job(model)

    return success_response(
        WorkflowDuplicateResponse(
            id=model.id,
            workflow_id=model.workflow_id,
            name=model.name,
            message="工作流复制成功",
        )
    )


@router.post(
    "/workflows/fork",
    summary="Fork 工作流（类似 GitHub）",
    response_model=StandardResponse[WorkflowForkResponse],
)
async def fork_workflow(
    request: WorkflowForkRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowForkResponse]:
    """Fork 工作流

    - 如果是自己的工作流：允许无条件 Fork（类似“创建副本”）
    - 如果是别人的工作流：仅允许 Fork 公开的工作流（类似 GitHub）

    Args:
        request: {"id": <工作流ID>, "new_name": <新名称（可选）>,
                  "auto_sync_upstream": <是否开启上游自动同步，默认 false>}
    """
    # 获取原工作流
    original = await workflow_crud_svr.get_by_id(request.id)
    if not original:
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    # 检查权限：如果是别人的工作流，必须是公开的
    if str(original.mid) != str(auth.mid) and not original.is_public:
        return error_response(403, "只能 Fork 公开的工作流或自己的工作流")

    try:
        # 执行 Fork（递归深拷贝 Action 依赖树）
        model, forked_action_count = await workflow_crud_svr.fork(
            id=request.id,
            target_mid=auth.mid,
            new_name=request.new_name,
            auto_sync_upstream=request.auto_sync_upstream,
        )

        if not model:
            return error_response(500, "Fork 失败")

        msg = "Fork 成功"
        if forked_action_count > 0:
            msg += f"，已独立复制 {forked_action_count} 个关联动作"

        return success_response(
            WorkflowForkResponse(
                id=model.id,
                workflow_id=model.workflow_id,
                name=model.name,
                forked_from=original.workflow_id,
            ),
            msg=msg,
        )
    except ValueError as e:
        # ValueError 包含 pydantic 校验失败，此前静默返回 400，日志无痕
        logger.opt(exception=True).error(
            f"Fork 工作流失败 workflow_id={request.id}: {e}"
        )
        return error_response(400, str(e))
    except Exception as e:
        from app.models.common.exceptions.base_exception import BaseException as BizBaseException

        if isinstance(e, BizBaseException):
            return error_response(e.code or 400, str(e))
        raise


@router.post(
    "/workflows/sync",
    summary="同步工作流到上游最新版本",
    response_model=StandardResponse[WorkflowSyncResponse],
)
async def sync_workflow(
    request: WorkflowSyncRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowSyncResponse]:
    """将 Fork 的工作流同步到原作者最新版本

    同步内容包括：
    - 工作流触发配置（trigger_type/trigger_config）
    - 关联的 Action 依赖树（递归同步所有 Fork 副本）
    - 上游新增的依赖会自动 Fork
    - 上游已删除/私有化的依赖保留当前副本并报告
    """
    try:
        result = await workflow_crud_svr.sync_from_upstream(
            id=request.id, mid=auth.mid
        )

        if result is None:
            return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

        msg_parts = []
        if result.get("workflow_config_updated"):
            msg_parts.append("工作流配置已更新")
        if result["updated"]:
            msg_parts.append(f"更新 {len(result['updated'])} 个动作")
        if result["created"]:
            msg_parts.append(f"新增 {len(result['created'])} 个动作")
        if result["unavailable"]:
            msg_parts.append(
                f"{len(result['unavailable'])} 个上游动作不可用（已保留当前版本）"
            )
        if result.get("local_overwritten"):
            msg_parts.append("已覆盖本地修改")
        msg = "同步完成：" + "，".join(msg_parts) if msg_parts else "已是最新版本"

        return success_response(
            WorkflowSyncResponse(
                workflow_id=result["workflow_id"],
                updated=result["updated"],
                created=result["created"],
                unavailable=result["unavailable"],
                workflow_config_updated=result.get(
                    "workflow_config_updated", False
                ),
                local_overwritten=bool(result.get("local_overwritten")),
            ),
            msg=msg,
        )
    except ValueError as e:
        logger.opt(exception=True).error(
            f"同步工作流失败 workflow_id={request.id}: {e}"
        )
        return error_response(400, str(e))
    except Exception as e:
        from app.models.common.exceptions.base_exception import BaseException as BizBaseException

        if isinstance(e, BizBaseException):
            return error_response(e.code or 400, str(e))
        raise


@router.get("/workflows/{id}/forks", summary="获取工作流的所有 Fork 版本")
async def get_workflow_forks(
    id: int,
    skip: int = 0,
    limit: int = 50,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[WorkflowListItemResponse]]:
    """获取某工作流的所有 Fork 版本列表"""
    original = await workflow_crud_svr.get_by_id(id)
    if not original:
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在")

    forks = await workflow_crud_svr.list_forks(id, skip, limit)

    # 批量计算上游同步状态
    sync_flags = await workflow_crud_svr.attach_upstream_update_flags(forks)

    items = [
        WorkflowListItemResponse(
            id=f.id,
            workflow_id=f.workflow_id,
            name=f.name,
            description=f.description,
            is_enabled=f.is_enabled,
            is_public=f.is_public,
            likes_count=f.likes_count,
            reports_count=f.reports_count,
            is_verified=f.is_verified,
            forks_count=f.forks_count,
            forked_from_id=f.forked_from_id,
            notify_inbox_on_start=f.notify_inbox_on_start,
            notify_inbox_on_end=f.notify_inbox_on_end,
            notify_push_on_start=f.notify_push_on_start,
            notify_push_on_end=f.notify_push_on_end,
            notify_on_start=f.notify_on_start,
            notify_on_end=f.notify_on_end,
            log_enabled=f.log_enabled,
            log_debug=f.log_debug,
            created_at=f.created_at,
            updated_at=f.updated_at,
            **_upstream_sync_kwargs(f, sync_flags.get(f.id)),
        )
        for f in forks
    ]

    pagination = BasePaginationResp[WorkflowListItemResponse](
        page=1, per_page=limit, total=len(items), items=items
    )

    return success_response(pagination)


@router.post(BrowserControlRouterPath.workflows_execute_step, summary="单步执行工作流")
async def execute_workflow_step(
    request: WorkflowStepExecuteRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowStepExecuteResponse]:
    """单步执行步骤列表中的指定一项（调试用）

    与「运行工作流」无关：调用方把整份 `steps` 传进来，本接口只执行
    `steps[step_index]` 这一项，不读数据库里的工作流定义、也不写运行记录。
    目标浏览器取自 body 的 `browser_id`（数字字符串）与 `page_index`。
    """
    try:
        if request.step_index < 0 or request.step_index >= len(request.steps):
            return error_response(
                code=400,
                msg=f"步骤索引 {request.step_index} 超出范围（总共 {len(request.steps)} 步）",
            )

        step = request.steps[request.step_index]
        logger.info(
            f"[Workflow Step Execute] 执行步骤 {request.step_index + 1}/{len(request.steps)}: {step.action_id}"
        )

        mid = auth.mid
        bid = int(request.browser_id) if request.browser_id.isdigit() else 0
        page = await _resolve_page(mid, bid, request.page_index)

        step_req = ActionExecutionRequest(
            mid=mid,
            browser_id=bid,
            variables=request.user_data,
            page_index=request.page_index,
            action_id=step.action_id,
            params=step.params or {},
        )
        result = await execution_engine.execute_action(
            step_req,
            session_id=str(bid),
            browser_id=str(bid),
            page=page,
        )

        logger.info(f"[Workflow Step Execute] 步骤执行完成: success={result.success}")

        return success_response(
            WorkflowStepExecuteResponse(
                success=result.success,
                step_index=request.step_index,
                action_id=step.action_id,
                result=result.data,
                error=result.error,
                duration=result.execution_time * 1000,
                current_step=request.step_index,
                total_steps=len(request.steps),
            )
        )
    except CustomBaseException:
        # 业务异常（1007 浏览器未启动 / 2016 工作流执行中）交给全局处理器，
        # 保留 code / msg / HTTP 语义，不能被下面的 except Exception 吞成 500
        raise
    except ValueError as e:
        return error_response(400, str(e))
    except Exception as e:
        logger.error(f"[Workflow Step Execute] 执行失败: {e}")
        return error_response(500, f"执行步骤失败: {str(e)}")


# ============ 调度外壳：立即运行 / 运行记录 ============


@router.post(BrowserControlRouterPath.workflows_run, summary="立即运行已保存的工作流")
async def run_saved_workflow(
    request: WorkflowRunNowRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[dict]:
    """立即运行一次已保存的工作流

    工作流是调度外壳：此处按工作流引用的动作顺序执行其步骤，并写入运行记录。

    目标浏览器**必须**通过 query 的 `browser_id` 指定（由 verify_browser_ownership
    校验归属，缺失直接 422）；body 里的 `browser_id` 仅为兼容旧调用保留，不参与决策，
    也不会回落到工作流配置的浏览器。

    执行是**异步**的：本接口立即返回 ``run_id``（``status=running``），真正的执行在
    后台 task 中进行；前端凭 run_id 轮询运行记录或调用停止接口。
    """
    mid = browser_info.auth_info.mid
    browser_id = int(browser_info.browser_id)

    model = await workflow_crud_svr.get_by_id(request.id)
    if not model or str(model.mid) != str(mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在或无权限")
    if not model.custom_action_id:
        return error_response(ResponseCode.BUSINESS_ERROR, "工作流未关联任何动作")

    await assert_approved("workflow", model.workflow_id, "execute")

    # 立即返回：后台 task 执行，run_id 已预生成，与最终落库记录一致
    run_id = await launch_workflow_run(
        model,
        trigger_source=WorkflowRunTriggerEnum.MANUAL,
        browser_id=browser_id,
        auth_headers=_build_auth_headers(browser_info.auth_info),
    )
    return success_response(
        {
            "run_id": run_id,
            "workflow_id": model.workflow_id,
            "browser_id": browser_id,
            # 多工作流并行（见 docs/多页面并行执行计划书.md §3）：受理即执行，
            # 不再排队；queue_position 保留字段但恒为 0
            "status": WorkflowRunStatusEnum.RUNNING.value,
            "queue_position": 0,
        }
    )


async def _stop_workflow_siblings(
    run: WorkflowRunRecord, mid: str
) -> tuple[bool, list[str]]:
    """停止同工作流的其它进行中运行，并关闭该工作流的定期执行。

    见 ``docs/工作流停止计划书.md``：用户点「停止」针对的是「这个工作流」，不只是
    当前这一条运行记录 —— 多选浏览器 / 并发 / 定时触发都可能同时存在多条运行，
    只停一条其余仍会继续跑，表现为「停止没生效」；同时还要让随后的 cron 触发不再
    拉起新运行（否则表现成「停了自己又跑起来」）。

    Returns:
        (是否关闭了定期执行, 被一并停止的其它 run_id 列表)。
        记录归属不匹配时不动作，只处理调用方指定的那一条运行。
    """
    workflow = await workflow_crud_svr.get_by_workflow_id(run.workflow_id)
    if not workflow or str(workflow.mid) != str(mid):
        return False, []

    stopped: list[str] = []
    siblings = await workflow_run_crud_svr.list_active_of_workflow(
        run.workflow_id, mid, exclude_run_id=run.run_id
    )
    for sibling in siblings:
        if sibling.status == WorkflowRunStatusEnum.QUEUED:
            if await workflow_run_crud_svr.finish_queued(
                sibling.run_id,
                status=WorkflowRunStatusEnum.CANCELLED,
                error_message="用户停止工作流：取消排队",
            ):
                stopped.append(sibling.run_id)
            continue
        if workflow_run_tasks.is_alive(sibling.run_id):
            # 与主运行一致：先置标记（收口时改写为 cancelled），再立刻强制中断
            cancellation_registry.request_cancel(sibling.run_id)
            workflow_run_tasks.cancel(sibling.run_id)
            stopped.append(sibling.run_id)
            continue
        # 僵尸记录（本实例无存活 task）→ 清理为中断
        if await workflow_run_crud_svr.interrupt_stale_running(
            reason="用户停止工作流：运行实例已不存在，已清理残留运行记录",
            run_ids=[sibling.run_id],
        ):
            stopped.append(sibling.run_id)

    schedule_stopped = await disable_workflow_schedule(workflow)
    return schedule_stopped, stopped


@router.post(BrowserControlRouterPath.workflows_runs_stop, summary="停止正在运行的工作流")
async def stop_workflow_run(
    request: WorkflowRunStopRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowRunStopResponse]:
    """手动停止一次工作流运行（幂等，立刻停止 + 关闭定期执行）

    停止语义：
      1. **立刻停止**：置取消标记后**直接** ``task.cancel()`` 中断执行协程，
         不再等步骤边界、也不等 30s 宽限期（见 ``docs/工作流停止计划书.md`` §3.1）；
      2. **定期执行也停止**：关闭该工作流的定时任务（``is_enabled=False`` +
         移除 APScheduler job），否则下一个 cron 触发点会再拉起一次运行；
      3. 同工作流仍在排队的其它运行一并取消。
    对**已结束**的运行本接口幂等返回其最终状态，不报错。

    分四种情形：
      0. 仍在排队（QUEUED）→ 直接出队并标取消；
      1. 运行已结束（非 RUNNING）→ 幂等返回最终状态；
      2. 本实例确有存活运行 task → 立刻强制中断，运行记录收口为 ``cancelled``；
      3. 记录仍为 RUNNING 但本实例无存活 task（进程重启 / 协程被取消的僵尸记录）→
         直接清理为 ``interrupted``，并清掉会话上残留的执行期标记。

    存活真值取自本实例运行 task 注册表（``workflow_run_tasks``）——**不**以会话标记
    ``entry.workflow_run_id`` 为存活依据：该标记正常由 ``end_workflow_run`` 解除，
    崩溃时会残留，若据此判断会把僵尸误当活运行，导致永远无法停止（用户痛点）。
    """
    run = await workflow_run_crud_svr.get_by_run_id(request.run_id, auth.mid)
    if not run:
        return error_response(ResponseCode.WORKFLOW_RUN_NOT_FOUND, "运行记录不存在")

    # 情形 0：仍在排队 → 直接出队并标取消（不占用执行权）
    if run.status == WorkflowRunStatusEnum.QUEUED:
        # 多工作流并行后无运行队列：QUEUED 只是「受理→执行」的极短过渡态
        removed = False
        await workflow_run_crud_svr.finish_queued(
            run.run_id,
            status=WorkflowRunStatusEnum.CANCELLED,
            error_message="用户取消排队",
        )
        schedule_stopped, cancelled_runs = await _stop_workflow_siblings(
            run, auth.mid
        )
        logger.info(
            f"[WorkflowRouter] 用户取消排队: mid={auth.mid}, run={run.run_id}, "
            f"removed_from_queue={removed}, 关闭定期执行={schedule_stopped}"
        )
        return success_response(
            WorkflowRunStopResponse(
                run_id=run.run_id,
                status=WorkflowRunStatusEnum.CANCELLED.value,
                queue_cancelled=True,
                schedule_stopped=schedule_stopped,
                cancelled_runs=cancelled_runs,
            )
        )

    # 情形 1：已结束 → 幂等返回最终状态（重复点击停止不应报错）
    if run.status != WorkflowRunStatusEnum.RUNNING:
        # 用户明确点了「停止工作流」：即使本次运行已结束，也一并关掉定期执行，
        # 否则下一次 cron 触发仍会把工作流重新拉起来
        schedule_stopped, cancelled_runs = await _stop_workflow_siblings(
            run, auth.mid
        )
        return success_response(
            WorkflowRunStopResponse(
                run_id=run.run_id,
                status=run.status.value,
                already_finished=True,
                schedule_stopped=schedule_stopped,
                cancelled_runs=cancelled_runs,
            )
        )

    # 情形 2：本实例有存活运行 task → 立刻强制中断（不等步骤边界 / 宽限期）
    if workflow_run_tasks.is_alive(run.run_id):
        # 顺序不能反：先置取消标记，收口时据此把终态改写为 cancelled；
        # 再直接 cancel() 打断当前 await，实现「点了就停」
        cancellation_registry.request_cancel(run.run_id)
        forced = workflow_run_tasks.cancel(run.run_id)
        schedule_stopped, cancelled_runs = await _stop_workflow_siblings(
            run, auth.mid
        )
        logger.info(
            f"[WorkflowRouter] 用户请求停止运行（立刻中断）: mid={auth.mid}, "
            f"run={run.run_id}, forced={forced}, 关闭定期执行={schedule_stopped}"
        )
        return success_response(
            WorkflowRunStopResponse(
                run_id=run.run_id,
                status=run.status.value,
                stopping=True,
                forced=forced,
                schedule_stopped=schedule_stopped,
                cancelled_runs=cancelled_runs,
            )
        )

    # 情形 3：僵尸记录（本实例已无存活 task）→ 复用「任务结束收尾」统一清理：
    # 清 task 句柄 / RUNNING→INTERRUPTED / 清会话执行期标记 / 清取消标记，
    # 不再在停止路径里另写一份清理逻辑
    reaped = await finalize_run_after_task(run.run_id)
    logger.warning(
        f"[WorkflowRouter] 用户停止清理残留运行: mid={auth.mid}, "
        f"run={run.run_id}, reaped={bool(reaped)}"
    )
    schedule_stopped, cancelled_runs = await _stop_workflow_siblings(run, auth.mid)
    # 收口接口返回的记录内存状态可能未刷新，重新读取以回报真实终态
    refreshed = await workflow_run_crud_svr.get_by_run_id(run.run_id, auth.mid)
    final_status = (
        refreshed.status if refreshed else WorkflowRunStatusEnum.INTERRUPTED
    )
    return success_response(
        WorkflowRunStopResponse(
            run_id=run.run_id,
            status=final_status.value,
            reaped=bool(reaped),
            schedule_stopped=schedule_stopped,
            cancelled_runs=cancelled_runs,
        )
    )


@router.post(BrowserControlRouterPath.workflows_runs, summary="查询工作流运行记录")
async def list_workflow_runs(
    request: WorkflowRunLogListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[WorkflowRunLogItemResponse]]:
    """分页查询某工作流的运行记录（最新在前）"""
    model = await workflow_crud_svr.get_by_workflow_id(request.workflow_id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在或无权限")

    skip = (request.page - 1) * request.per_page
    total = await workflow_run_crud_svr.count_by_workflow(request.workflow_id)
    rows = await workflow_run_crud_svr.list_by_workflow(
        request.workflow_id, skip=skip, limit=request.per_page
    )
    items = [
        WorkflowRunLogItemResponse(
            **(await workflow_run_crud_svr.to_dict(row)),
            queue_position=_queue_position(row),
        )
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


@router.post(
    BrowserControlRouterPath.workflows_resources_list,
    summary="分页查询工作流已处理资源记录",
)
async def list_workflow_resources(
    request: WorkflowResourceRecordListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[WorkflowResourceRecordItemResponse]]:
    """查看工作流已处理（去重）数据。

    返回记录主键 `id` 与业务主键 `resource_id`，以及状态、处理次数、所属运行等；
    `status` 不传则返回全部（含 failed），前端默认建议只看 success/skipped。
    """
    model = await workflow_crud_svr.get_by_workflow_id(request.workflow_id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在或无权限")

    status_filter: ResourceProcessStatus | None = None
    if request.status:
        try:
            status_filter = ResourceProcessStatus(request.status)
        except ValueError:
            return error_response(
                400, f"无效的处理状态: {request.status}（success/skipped/failed）"
            )

    resp = await ResourceRecordService.list_records(
        auth.mid,
        request.workflow_id,
        BasePaginationReq(page=request.page, per_page=request.per_page),
        browser_id=request.browser_id,
        resource_type=request.resource_type,
        status=status_filter,
        resource_id=request.resource_id,
        execution_id=request.execution_id,
        process_count_min=request.process_count_min,
        process_count_max=request.process_count_max,
        processed_from=request.processed_from,
        processed_to=request.processed_to,
    )
    items = [
        WorkflowResourceRecordItemResponse(
            id=int(row.id or 0),
            workflow_id=row.workflow_id,
            browser_id=row.browser_id,
            resource_type=row.resource_type,
            resource_id=str(row.resource_id),
            status=str(row.status),
            process_count=row.process_count,
            fail_reason=row.fail_reason,
            execution_id=row.execution_id,
            extra=row.extra,
            first_processed_at=row.first_processed_at,
            last_processed_at=row.last_processed_at,
        )
        for row in resp.items
    ]
    return success_response(
        BasePaginationResp[WorkflowResourceRecordItemResponse](
            page=resp.page,
            per_page=resp.per_page,
            total=resp.total,
            items=items,
        )
    )


def _parse_resource_status(raw: str | None) -> ResourceProcessStatus | None:
    """解析资源处理状态字符串（None/空 = 不过滤）；非法值抛 ValueError。"""
    if not raw:
        return None
    return ResourceProcessStatus(str(raw).strip().lower())


def _resource_item_response(
    row,
) -> WorkflowResourceRecordItemResponse:
    """把 WorkflowResourceRecord 转成接口响应项（唯一映射点）。"""
    return WorkflowResourceRecordItemResponse(
        id=int(row.id or 0),
        workflow_id=row.workflow_id,
        browser_id=row.browser_id,
        resource_type=row.resource_type,
        resource_id=str(row.resource_id),
        status=str(row.status),
        process_count=row.process_count,
        fail_reason=row.fail_reason,
        execution_id=row.execution_id,
        extra=row.extra,
        first_processed_at=row.first_processed_at,
        last_processed_at=row.last_processed_at,
    )


@router.post(
    BrowserControlRouterPath.workflows_resources_create,
    summary="手工新增/更新已处理资源记录",
)
async def create_workflow_resource(
    request: WorkflowResourceRecordCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowResourceRecordItemResponse]:
    """新增一条「已处理资源」记录（已存在则更新状态并累加处理次数）。

    用于修正去重数据：确认线下已参与、需要提前加入去重集合，或把误记的项改成
    `failed`（failed 不参与去重，下一轮会被重新捞起）。
    """
    model = await workflow_crud_svr.get_by_workflow_id(request.workflow_id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在或无权限")

    try:
        status = _parse_resource_status(request.status) or ResourceProcessStatus.SUCCESS
    except ValueError:
        return error_response(
            400, f"无效的处理状态: {request.status}（success/skipped/failed）"
        )

    browser_id = (
        int(request.browser_id)
        if request.browser_id is not None
        else int(model.browser_id or 0)
    )
    resource_id = request.resource_id.strip()
    if not resource_id:
        return error_response(400, "resource_id 不能为空")

    await ResourceRecordService.upsert_result(
        auth.mid,
        request.workflow_id,
        browser_id,
        request.resource_type,
        resource_id,
        status == ResourceProcessStatus.SUCCESS,
        status=status,
        fail_reason=request.fail_reason,
        extra=request.extra,
    )
    row = await ResourceRecordService.get_record(
        auth.mid,
        request.workflow_id,
        browser_id,
        request.resource_type,
        resource_id,
    )
    if row is None:
        return error_response(500, "写入后未能读回记录，请重试")
    logger.info(
        f"[WorkflowRouter] 手工写入资源记录: workflow={request.workflow_id}, "
        f"browser={browser_id}, type={request.resource_type}, "
        f"id={resource_id}, status={status}"
    )
    return success_response(_resource_item_response(row))


@router.post(
    BrowserControlRouterPath.workflows_resources_delete,
    summary="删除已处理资源记录",
)
async def delete_workflow_resources(
    request: WorkflowResourceRecordDeleteRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowResourceRecordDeleteResponse]:
    """删除「已处理资源」记录（下次取数会被重新捞起）。

    支持三种用法：
      - 列表页勾选：传 `ids`（记录主键）；
      - 单条/条件清理：传 `resource_type` + `resource_id`（精确）等；
      - 清理某一轮执行的误记：传 `execution_id`（执行批次 ID，**不是** run_id）。

    `dry_run=true` 只返回将删除的条数，供前端二次确认。
    """
    model = await workflow_crud_svr.get_by_workflow_id(request.workflow_id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(ResponseCode.WORKFLOW_NOT_FOUND, "工作流不存在或无权限")

    try:
        status = _parse_resource_status(request.status)
    except ValueError:
        return error_response(
            400, f"无效的处理状态: {request.status}（success/skipped/failed）"
        )

    try:
        deleted = await ResourceRecordService.delete_records(
            auth.mid,
            request.workflow_id,
            ids=request.ids,
            browser_id=request.browser_id,
            resource_type=request.resource_type,
            status=status,
            resource_id=request.resource_id,
            execution_id=request.execution_id,
            dry_run=request.dry_run,
        )
    except ValueError as exc:
        return error_response(400, str(exc))

    logger.info(
        f"[WorkflowRouter] 删除资源记录: workflow={request.workflow_id}, "
        f"ids={request.ids}, type={request.resource_type}, status={status}, "
        f"execution={request.execution_id}, dry_run={request.dry_run}, count={deleted}"
    )
    return success_response(
        WorkflowResourceRecordDeleteResponse(
            deleted=deleted, dry_run=request.dry_run
        )
    )


@router.post(
    BrowserControlRouterPath.workflows_runs_get, summary="获取工作流运行记录详情"
)
async def get_workflow_run(
    request: WorkflowRunDetailRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowRunLogDetailResponse]:
    """按 run_id 获取运行记录详情（返回体与运行记录列表项同构）。

    Args:
        request: {"run_id": "<运行ID>"}
    """
    model = await workflow_run_crud_svr.get_by_run_id(request.run_id, auth.mid)
    if not model:
        return error_response(ResponseCode.WORKFLOW_RUN_NOT_FOUND, "运行记录不存在")
    return success_response(
        WorkflowRunLogDetailResponse(
            **(await workflow_run_crud_svr.to_dict(model)),
            queue_position=_queue_position(model),
        )
    )


@router.get(
    BrowserControlRouterPath.workflows_runs_events,
    response_class=StreamingResponse,
    # 必须显式声明 SSE 内容类型：openapi.json 才能如实描述契约，
    # hey-api 依据 mediaType === "text/event-stream" 才会生成 client.sse.* 方法。
    responses={
        200: {
            "description": "工作流运行进度 SSE 事件流（text/event-stream 长连接）",
            "content": {"text/event-stream": {}},
        }
    },
)
async def workflow_run_events(
    run_id: str,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """订阅某次工作流运行的进度事件流（Server-Sent Events）。

    同一条连接承载两类事件：
      - `workflow_run_progress`：全量进度快照（首帧 + 步骤/循环边界更新）；
      - `workflow_run_log`：步骤级明细（开始/完成/失败、循环轮次、运行结束），
        供前端「运行消息」栏逐条展示，建连时会补发最近 200 条。

    终态（success/failed/interrupted/cancelled）帧下发后服务端关闭连接。

    Args:
        run_id: 运行ID（query 参数）
    """
    run = await workflow_run_crud_svr.get_by_run_id(run_id, auth.mid)
    if not run:
        return error_response(ResponseCode.WORKFLOW_RUN_NOT_FOUND, "运行记录不存在")

    workflow = await workflow_crud_svr.get_by_workflow_id(run.workflow_id)
    initial = WorkflowRunProgressData.from_run_record(
        run, workflow.name if workflow else ""
    )
    reporter = get_run_reporter(run_id)
    return StreamingResponse(
        iter_workflow_run_events(
            initial,
            heartbeat_interval=settings.workflow_run_sse_heartbeat_interval,
            backlog=reporter.history() if reporter else None,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 若前面挂 nginx，必须关闭响应缓冲，否则推送会被攒批
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    BrowserControlRouterPath.workflows_runs_resume,
    summary="继续运行被中断的工作流（断点恢复）",
)
async def resume_workflow_run(
    request: WorkflowRunResumeRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """手动继续一条被服务中断的运行（不受工作流自动恢复开关约束）。

    恢复为**至少一次**语义：从检查点之后继续执行，当前步骤重跑；循环内已处理资源由
    处理记录去重跳过。同一浏览器已有运行时不打断，返回业务错误引导稍后重试。
    """
    run = await workflow_run_crud_svr.get_by_run_id(request.run_id, auth.mid)
    if not run:
        return error_response(ResponseCode.WORKFLOW_RUN_NOT_FOUND, "运行记录不存在")
    if run.status != WorkflowRunStatusEnum.INTERRUPTED:
        return error_response(
            ResponseCode.BUSINESS_ERROR, "仅已中断的运行可以继续，当前状态不支持恢复"
        )
    if not run.resumable:
        return error_response(
            ResponseCode.BUSINESS_ERROR,
            "该运行不可恢复（无已完成步骤 / 已被恢复过 / 已超过恢复次数上限）",
        )

    new_run_id = await resume_run_record(run, require_auto_switch=False)
    if not new_run_id:
        return error_response(
            ResponseCode.BUSINESS_ERROR,
            "恢复失败：目标浏览器当前可能有运行中的任务，或工作流已停用 / 恢复次数超限",
        )
    return success_response(
        {
            "run_id": new_run_id,
            "resume_of": run.run_id,
            "status": WorkflowRunStatusEnum.RUNNING.value,
        }
    )
