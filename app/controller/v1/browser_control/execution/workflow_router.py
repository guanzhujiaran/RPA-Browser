"""
Workflow 管理路由

提供工作流（Workflow）的 CRUD 和执行 API
"""
from app.services.execution.engine import ExecutionEngine
from loguru import logger
import uuid
from bili_common.models.response import StandardResponse, success_response, error_response
from bili_common.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.admin_depends import assert_approved
from app.utils.depends.security_depends import verify_browser_ownership
from bili_common.models.depends import BrowserReqAuthInfo
from fastapi import Depends
from app.services.RPA_browser.session.live_service import live_service
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
)
from app.services.execution.workflow_runner import run_workflow
from app.models.database.workflow.models import WorkflowRunTriggerEnum
from app.models.workflow.models import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowListRequest,
    WorkflowDetailResponse,
    WorkflowListItemResponse,
    WorkflowCreateResponse,
    WorkflowDuplicateResponse,
    WorkflowForkRequest,
    WorkflowForkResponse,
    WorkflowStepExecuteRequest,
    WorkflowStepExecuteResponse,
    WorkflowRunNowRequest,
    WorkflowRunLogListRequest,
    WorkflowRunLogItemResponse,
    WorkflowRunLogDetailResponse,
)
from app.models.base.base_sqlmodel import BasePaginationResp
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


def _validate_trigger(trigger_type: str, trigger_config: dict | None, browser_id: int | None) -> str | None:
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
    """从 LiveService 解析浏览器页面"""
    entry = live_service.get_browser_session_entry(mid=mid, browser_id=browser_id)
    if not entry:
        raise ValueError("浏览器不存在或未运行")
    if page_index is not None:
        all_pages = entry.browser_session.all_pages
        if not all_pages:
            raise ValueError(f"浏览器 {browser_id} 没有打开任何页面")
        if not (0 <= page_index < len(all_pages)):
            raise ValueError(f"页面索引 {page_index} 超出范围 (0-{len(all_pages)-1})")
        return all_pages[page_index]
    else:
        return await entry.browser_session.get_current_page()

router = new_workflow_router()


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
                ResponseCode.BUSINESS_ERROR, f"引用的动作不存在: {request.custom_action_id}"
            )

    # 生成唯一的 workflow_id
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"

    model = await workflow_crud_svr.create(
        workflow_id=workflow_id,
        name=request.name,
        mid=auth.mid,
        custom_action_id=request.custom_action_id,
        description=request.description,
        browser_id=request.browser_id,
        trigger_type=request.trigger_type,
        trigger_config=request.trigger_config,
        is_public=request.is_public,
        enabled_plugins=request.enabled_plugins,
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
    total = await workflow_crud_svr.count_by_user(
        mid=auth.mid,
        filter_type=request.filter_type
    )

    # 获取列表数据
    models = await workflow_crud_svr.list_by_user(
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
                custom_action_id=model.custom_action_id,
                description=model.description,
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

    model = await workflow_crud_svr.get_by_id(workflow_id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(404, "工作流不存在")

    # 获取关联的插件列表
    enabled_plugins = await workflow_crud_svr.get_enabled_plugins(model.workflow_id)

    return success_response(
        WorkflowDetailResponse(
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
            enabled_plugins=enabled_plugins,
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
    existing = await workflow_crud_svr.get_by_id(request.id)
    if not existing or str(existing.mid) != str(auth.mid):
        return error_response(404, "工作流不存在或无权限")

    # publish 审批强制：把工作流公开到社区前，需已通过对应 publish 审批单（未通过保持 private）
    if request.is_public is True and not existing.is_public:
        await assert_approved("workflow", existing.workflow_id, "publish")

    # 触发配置校验：按「合并后」的最终值校验（未传的字段沿用原值）
    final_trigger_type = request.trigger_type if request.trigger_type is not None else existing.trigger_type
    final_trigger_config = request.trigger_config if request.trigger_config is not None else (existing.trigger_config or {})
    final_browser_id = request.browser_id if request.browser_id is not None else existing.browser_id
    trigger_error = _validate_trigger(final_trigger_type, final_trigger_config, final_browser_id)
    if trigger_error:
        return error_response(ResponseCode.INVALID_PARAM, trigger_error)

    # 引用的动作必须存在（工作流不改写动作内容）
    if request.custom_action_id:
        action_model = await action_crud_svr.get_by_action_id(request.custom_action_id)
        if not action_model:
            return error_response(
                ResponseCode.BUSINESS_ERROR, f"引用的动作不存在: {request.custom_action_id}"
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
        enabled_plugins=request.enabled_plugins,
    )

    if not model or str(model.mid) != str(auth.mid):
        return error_response(404, "工作流不存在或无权限")

    # 同步定时任务（启用/cron/浏览器任一变化都需重建或移除）
    await sync_workflow_job(model)
    model = await workflow_crud_svr.get_by_id(model.id) or model

    # 获取关联的插件列表
    enabled_plugins = await workflow_crud_svr.get_enabled_plugins(model.workflow_id)

    return success_response(
        WorkflowDetailResponse(
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
            enabled_plugins=enabled_plugins,
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

    model = await workflow_crud_svr.get_by_id(workflow_id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(404, "工作流不存在或无权限")

    # 先摘掉定时任务，避免删除后仍被调度
    remove_workflow_job(model.workflow_id)

    success = await workflow_crud_svr.delete(workflow_id)
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
    original = await workflow_crud_svr.get_by_id(workflow_id)
    if not original or str(original.mid) != str(auth.mid):
        return error_response(404, "工作流不存在或无权限")

    # 创建副本
    new_workflow_id = f"wf_{uuid.uuid4().hex[:12]}"

    # 复制原工作流的插件关联
    original_plugins = await workflow_crud_svr.get_enabled_plugins(original.workflow_id)

    model = await workflow_crud_svr.create(
        workflow_id=new_workflow_id,
        name=new_name,
        mid=auth.mid,
        custom_action_id=original.custom_action_id,
        description=f"复制自: {original.name}",
        browser_id=original.browser_id,
        trigger_type=original.trigger_type,
        trigger_config=original.trigger_config,
        is_public=False,
        enabled_plugins=original_plugins,
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
    original = await workflow_crud_svr.get_by_id(request.id)
    if not original:
        return error_response(404, "工作流不存在")

    # 检查权限：如果是别人的工作流，必须是公开的
    if str(original.mid) != str(auth.mid) and not original.is_public:
        return error_response(403, "只能 Fork 公开的工作流或自己的工作流")

    try:
        # 执行 Fork
        model = await workflow_crud_svr.fork(
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
            msg="Fork 成功"
        )
    except ValueError as e:
        return error_response(400, str(e))


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
        return error_response(404, "工作流不存在")

    forks = await workflow_crud_svr.list_forks(id, skip, limit)

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
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in forks
    ]

    pagination = BasePaginationResp[WorkflowListItemResponse](
        page=1,
        per_page=limit,
        total=len(items),
        items=items
    )

    return success_response(pagination)


@router.post(BrowserControlRouterPath.workflows_execute_step, summary="单步执行工作流")
async def execute_workflow_step(
    request: WorkflowStepExecuteRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowStepExecuteResponse]:
    """单步执行工作流

    执行工作流中的指定步骤，用于调试和逐步执行
    """
    try:
        if request.step_index < 0 or request.step_index >= len(request.steps):
            return error_response(
                code=400,
                msg=f"步骤索引 {request.step_index} 超出范围（总共 {len(request.steps)} 步）"
            )

        step = request.steps[request.step_index]
        logger.info(
            f"[Workflow Step Execute] 执行步骤 {request.step_index + 1}/{len(request.steps)}: {step.action_id}")

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

        logger.info(
            f"[Workflow Step Execute] 步骤执行完成: success={result.success}")

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
    目标浏览器以 **query 的 browser_id 为准**（由 verify_browser_ownership 校验归属），
    body 中的 browser_id 仅作兼容保留、不参与决策；未指定浏览器时回落到工作流配置的 browser_id。
    """
    mid = browser_info.auth_info.mid
    browser_id = int(browser_info.browser_id)

    model = await workflow_crud_svr.get_by_id(request.id)
    if not model or str(model.mid) != str(mid):
        return error_response(404, "工作流不存在或无权限")
    if not model.custom_action_id:
        return error_response(ResponseCode.BUSINESS_ERROR, "工作流未关联任何动作")

    await assert_approved("workflow", model.workflow_id, "execute")

    result = await run_workflow(
        model,
        trigger_source=WorkflowRunTriggerEnum.MANUAL,
        browser_id=browser_id,
        auth_headers=_build_auth_headers(browser_info.auth_info),
    )
    return success_response(result)


@router.post(BrowserControlRouterPath.workflows_runs, summary="查询工作流运行记录")
async def list_workflow_runs(
    request: WorkflowRunLogListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[WorkflowRunLogItemResponse]]:
    """分页查询某工作流的运行记录（最新在前）"""
    model = await workflow_crud_svr.get_by_workflow_id(request.workflow_id)
    if not model or str(model.mid) != str(auth.mid):
        return error_response(404, "工作流不存在或无权限")

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


@router.post(BrowserControlRouterPath.workflows_runs_get, summary="获取工作流运行记录详情")
async def get_workflow_run(
    request: dict,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[WorkflowRunLogDetailResponse]:
    """按 run_id 获取运行记录详情

    Args:
        request: {"run_id": "<运行ID>"}
    """
    run_id = request.get("run_id")
    if not run_id:
        return error_response(400, "缺少 run_id")

    model = await workflow_run_crud_svr.get_by_run_id(run_id, auth.mid)
    if not model:
        return error_response(404, "运行记录不存在")
    return success_response(
        WorkflowRunLogDetailResponse(**(await workflow_run_crud_svr.to_dict(model)))
    )
