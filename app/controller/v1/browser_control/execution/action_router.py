"""
Action 管理路由

提供系统预注册 Action 和用户复合 Action（Composite Action）的 API
"""

from typing import Dict, List
from loguru import logger
from bili_common.models.response import (
    StandardResponse,
    success_response,
    error_response,
)
from bili_common.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.admin_depends import assert_approved
from fastapi import Depends
from app.services.execution.crud_service import action_crud_svr
from app.services.execution.crud_service.action_crud import ActionCrudService
from app.services.execution.action_logger import invalidate_cache
from app.models.workflow.models import (
    CompositeActionCreateRequest,
    CompositeActionUpdateRequest,
    CompositeActionListRequest,
    CompositeActionGetRequest,
    CompositeActionDeleteRequest,
    CompositeActionDetailResponse,
    CompositeActionListItemResponse,
    ActionForkRequest,
    ActionForkResponse,
    ActionSyncRequest,
    ActionSyncResponse,
    InputVarDefinition,
    TagSearchRequest,
    TagWithCount,
    NameSearchRequest,
    UpstreamSyncFlag,
)
from app.models.execution.action_params import (
    ActionMetadataResponse,
    BuiltinActionType,
    build_action_metadata,
)
from app.models.base.base_sqlmodel import BasePaginationResp
from app.models.common.exceptions.base_exception import BaseException as BizBaseException
from app.services.execution.variable_contract import compute_custom_action_variable_contract
from ..base import new_action_router

router = new_action_router()


def _convert_steps(steps: list) -> list[Dict]:
    """将 WorkflowStep 列表转换为 dict 列表"""
    return [
        step.model_dump() if hasattr(step, "model_dump") else step for step in steps
    ]


def _upstream_sync_kwargs(
    model, flag: UpstreamSyncFlag | None = None
) -> Dict:
    """构造响应中的上游同步状态字段（docs/fork-上游同步计划书.md）

    Args:
        model: CompositeActionModel
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
        # 上游版本号 / 内容指纹：前端可展示「上游 v1.3.0 · 你的副本 v1.1.0」
        "upstream_version": flag.upstream_version if flag else None,
        "upstream_content_hash": flag.upstream_content_hash if flag else "",
    }


# ============ 系统预注册操作（只读，全 POST） ============


@router.post(
    BrowserControlRouterPath.actions_registered, summary="获取系统预注册操作列表"
)
async def list_registered_actions() -> StandardResponse[List[ActionMetadataResponse]]:
    """获取系统预注册操作列表（公开，只读）

    返回每个内置动作的元数据：`action_id`（内置动作名）、`action_type`、`name`、
    图标（`icon_series` / `icon_id`）、参数定义 `json_schema`，
    以及变量契约 `input_variables` / `output_variables`。
    """
    response_actions = []
    for action_type in BuiltinActionType:
        meta = build_action_metadata(action_type)
        response_actions.append(
            ActionMetadataResponse(
                action_id=action_type.value,
                action_type=action_type,
                name=action_type.nameDisplay,
                # 内置操作的默认展示图标（由后端统一分配系列/编号，图库缺失时前端回落到内置图标）
                icon_series=meta.icon_series,
                icon_id=meta.icon_id,
                json_schema=meta.json_schema or {},
                # 变量契约（显式声明 + {{...}} 模板扫描兜底），前端据此渲染变量绑定区
                input_variables=meta.input_variables,
                output_variables=meta.output_variables,
            )
        )
    return success_response(response_actions)


# ============ 自定义操作管理（用户自定义 Action） ============


@router.post(BrowserControlRouterPath.custom_actions_create, summary="创建自定义操作")
async def create_custom_action(
    request: CompositeActionCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CompositeActionDetailResponse]:
    """创建用户自定义操作

    用户可以基于系统预注册的操作或插件组合创建自己的操作。
    action_id 由系统自动生成（雪花 int），用户仅需提供显示名称 name。
    """
    # 将 InputVarDefinition 对象转换为字典以便正确序列化
    input_vars_dicts = [
        var.model_dump() if hasattr(var, "model_dump") else dict(var)
        for var in (request.input_vars or [])
    ]

    model = await action_crud_svr.create(
        name=request.name,
        mid=auth.mid,
        description=request.description,
        version=request.version,
        steps=request.steps,
        tags=request.tags or [],
        input_vars=input_vars_dicts,
        output_vars=request.output_vars,
        icon_series=request.icon_series,
        icon_id=request.icon_id,
        timeout=request.timeout,
        retry_on_error=request.retry_on_error,
        retry_times=request.retry_times,
        retry_delay=request.retry_delay,
        is_public=request.is_public,
        log_enabled=request.log_enabled,
        log_debug=request.log_debug,
        log_record_params=request.log_record_params,
        log_record_result=request.log_record_result,
        log_record_variables=request.log_record_variables,
        log_record_page_snapshot=request.log_record_page_snapshot,
        log_only_on_error=request.log_only_on_error,
        log_max_payload_length=request.log_max_payload_length,
        log_retention_days=request.log_retention_days,
    )

    # 采集配置缓存按 action 维度 TTL 缓存，增删改后立即失效，避免改配置后需等 TTL 生效
    invalidate_cache(model.action_id)

    # 将字典转换回 InputVarDefinition 对象
    input_vars_objs = [
        InputVarDefinition(**var) if isinstance(var, dict) else var
        for var in (model.input_vars or [])
    ]

    # 从多对多关联表加载标签
    tags = await action_crud_svr.get_tags_for_action(model.id)

    return success_response(
        CompositeActionDetailResponse(
            action_id=model.action_id,
            name=model.name,
            version=model.version,
            content_hash=model.content_hash,
            action_type=model.action_type,
            description=model.description,
            icon_series=model.icon_series,
            icon_id=model.icon_id,
            mid=model.mid,
            parameters_schema=model.parameters_schema,
            steps=_convert_steps(model.steps),
            tags=tags,
            input_vars=input_vars_objs,
            output_vars=model.output_vars,
            is_enabled=model.is_enabled,
            is_public=model.is_public,
            timeout=model.timeout,
            retry_on_error=model.retry_on_error,
            retry_times=model.retry_times,
            retry_delay=model.retry_delay,
            log_enabled=model.log_enabled,
            log_debug=model.log_debug,
            log_record_params=model.log_record_params,
            log_record_result=model.log_record_result,
            log_record_variables=model.log_record_variables,
            log_record_page_snapshot=model.log_record_page_snapshot,
            log_only_on_error=model.log_only_on_error,
            log_max_payload_length=model.log_max_payload_length,
            log_retention_days=model.log_retention_days,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
            **_upstream_sync_kwargs(model),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(
    BrowserControlRouterPath.custom_actions_import, summary="从 JSON 导入自定义操作"
)
async def import_custom_action(
    request: CompositeActionCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CompositeActionDetailResponse]:
    """从 JSON 导入自定义操作

    请求体即「导出 JSON」内容（可携带 mid / id / created_at / version / likes_count
    等数据库冗余字段，自动忽略）。导入会在**当前用户名下新建**一份动作，
    action_id 由系统重新生成（雪花 int），不影响原动作。
    """
    if not request.steps:
        return error_response(ResponseCode.INVALID_PARAM, "导入内容缺少 steps 步骤列表")
    return await create_custom_action(request, auth)


@router.post(
    BrowserControlRouterPath.custom_actions_export, summary="导出自定义操作为 JSON"
)
async def export_custom_action(
    request: CompositeActionGetRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CompositeActionCreateRequest]:
    """导出自定义操作

    返回体与「创建 / 导入」请求体字段完全一致，可直接保存为 .json 后再由导入接口还原；
    仅本人或已公开的动作可导出。
    """
    action_id = request.action_id
    if not action_id:
        return error_response(400, "缺少操作action_id")

    model = await action_crud_svr.get_by_action_id(action_id)
    if not model:
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在")

    if model.mid != str(auth.mid) and not model.is_public:
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在")

    tags = await action_crud_svr.get_tags_for_action(model.id)
    input_vars_objs = [
        InputVarDefinition(**var) if isinstance(var, dict) else var
        for var in (model.input_vars or [])
    ]

    return success_response(
        CompositeActionCreateRequest(
            name=model.name,
            version=model.version,
            action_type=model.action_type,
            description=model.description,
            icon_series=model.icon_series,
            icon_id=model.icon_id,
            parameters_schema=model.parameters_schema or [],
            steps=_convert_steps(model.steps or []),
            tags=tags,
            input_vars=input_vars_objs,
            output_vars=model.output_vars or [],
            is_public=model.is_public,
            timeout=model.timeout,
            retry_on_error=model.retry_on_error,
            retry_times=model.retry_times,
            retry_delay=model.retry_delay,
            log_enabled=model.log_enabled,
            log_debug=model.log_debug,
            log_record_params=model.log_record_params,
            log_record_result=model.log_record_result,
            log_record_variables=model.log_record_variables,
            log_record_page_snapshot=model.log_record_page_snapshot,
            log_only_on_error=model.log_only_on_error,
            log_max_payload_length=model.log_max_payload_length,
            log_retention_days=model.log_retention_days,
        )
    )


@router.post(BrowserControlRouterPath.custom_actions_list, summary="获取自定义操作列表")
async def list_custom_actions(
    request: CompositeActionListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[CompositeActionListItemResponse]]:
    """获取当前用户的自定义操作列表

    支持分页、筛选、按名称搜索、按标签筛选和排序
    """
    # 计算 skip
    skip = (request.page - 1) * request.per_page

    # 获取总数
    total = await action_crud_svr.count_by_user(
        mid=auth.mid,
        filter_type=request.filter_type,
        name=request.name,
        tag=request.tag,
        tag_exact=request.tag_exact,
    )

    # 获取列表数据
    models = await action_crud_svr.list_by_user(
        mid=auth.mid,
        skip=skip,
        limit=request.per_page,
        filter_type=request.filter_type,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
        name=request.name,
        tag=request.tag,
        tag_exact=request.tag_exact,
    )

    # 批量加载标签
    tags_map = await action_crud_svr.get_tags_for_actions([m.id for m in models])

    # 批量计算上游同步状态（一次 IN 查询，避免 N+1）
    sync_flags = await action_crud_svr.attach_upstream_update_flags(models)

    items = [
        CompositeActionListItemResponse(
            id=model.id,
            action_id=model.action_id,
            name=model.name,
            version=model.version,
            content_hash=model.content_hash,
            action_type=model.action_type,
            description=model.description,
            icon_series=model.icon_series,
            icon_id=model.icon_id,
            steps_count=len(model.steps) if model.steps else 0,
            tags=tags_map.get(model.id, []),
            is_enabled=model.is_enabled,
            is_public=model.is_public,
            log_enabled=model.log_enabled,
            log_debug=model.log_debug,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
            **_upstream_sync_kwargs(model, sync_flags.get(model.id)),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
        for model in models
    ]

    # 构建分页响应
    pagination = BasePaginationResp[CompositeActionListItemResponse](
        page=request.page, per_page=request.per_page, total=total, items=items
    )

    return success_response(pagination)


@router.post(BrowserControlRouterPath.custom_actions_get, summary="获取自定义操作详情")
async def get_custom_action(
    request: CompositeActionGetRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CompositeActionDetailResponse]:
    """获取自定义操作详情，steps 中引用的其它复合动作会自动附加完整信息并校验权限"""
    action_id = request.action_id
    if not action_id:
        return error_response(400, "缺少操作action_id")

    model = await action_crud_svr.get_by_action_id(action_id)
    if not model:
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在")

    if model.mid != str(auth.mid) and not model.is_public:
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在")

    # 将字典转换回 InputVarDefinition 对象
    input_vars_objs = [
        InputVarDefinition(**var) if isinstance(var, dict) else var
        for var in (model.input_vars or [])
    ]

    # 解析 steps 并校验所有引用的其它复合动作的权限，同时获取完整详情
    steps_data = _convert_steps(model.steps)
    try:
        ca_ids = ActionCrudService._collect_ca_action_ids(steps_data)
        detail_map = (
            await action_crud_svr.get_validated_action_details(ca_ids, auth.mid)
            if ca_ids
            else {}
        )
        # 必须无条件调用：复合动作写 action_detail，内置动作写 icon_series/icon_id。
        # 此前整段被 `if ca_ids` 包住，纯内置步骤的动作拿不到图标，前端流程图只能回落通用图标。
        ActionCrudService._annotate_steps_with_action_details(steps_data, detail_map)
    except Exception as e:
        return error_response(
            getattr(e, "code", 403), str(e.msg) if hasattr(e, "msg") else str(e)
        )

    # 从多对多关联表加载标签
    tags = await action_crud_svr.get_tags_for_action(model.id)

    # 聚合步骤树得到对外变量契约（静态推导，失败不阻塞详情）

    try:
        input_variables, output_variables = (
            await compute_custom_action_variable_contract(steps_data)
        )
    except Exception:
        input_variables, output_variables = [], []

    # 上游同步状态（单条详情）
    sync_flags = await action_crud_svr.attach_upstream_update_flags([model])

    return success_response(
        CompositeActionDetailResponse(
            action_id=model.action_id,
            name=model.name,
            version=model.version,
            content_hash=model.content_hash,
            action_type=model.action_type,
            description=model.description,
            icon_series=model.icon_series,
            icon_id=model.icon_id,
            mid=model.mid,
            parameters_schema=model.parameters_schema,
            steps=steps_data,
            tags=tags,
            input_vars=input_vars_objs,
            output_vars=model.output_vars,
            input_variables=input_variables,
            output_variables=output_variables,
            is_enabled=model.is_enabled,
            is_public=model.is_public,
            timeout=model.timeout,
            retry_on_error=model.retry_on_error,
            retry_times=model.retry_times,
            retry_delay=model.retry_delay,
            log_enabled=model.log_enabled,
            log_debug=model.log_debug,
            log_record_params=model.log_record_params,
            log_record_result=model.log_record_result,
            log_record_variables=model.log_record_variables,
            log_record_page_snapshot=model.log_record_page_snapshot,
            log_only_on_error=model.log_only_on_error,
            log_max_payload_length=model.log_max_payload_length,
            log_retention_days=model.log_retention_days,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
            **_upstream_sync_kwargs(model, sync_flags.get(model.id)),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.custom_actions_update, summary="更新自定义操作")
async def update_custom_action(
    request: CompositeActionUpdateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CompositeActionDetailResponse]:
    """更新自定义操作"""
    # 先校验所有权，防止越权修改他人数据
    existing = await action_crud_svr.get_by_action_id(request.action_id)
    if not existing or existing.mid != str(auth.mid):
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在或无权限")

    # 将 InputVarDefinition 对象转换为字典以便正确序列化
    input_vars_dicts = None
    if request.input_vars is not None:
        input_vars_dicts = [
            var.model_dump() if hasattr(var, "model_dump") else dict(var)
            for var in request.input_vars
        ]

    # publish 审批强制：把操作公开到社区前，需已通过对应 publish 审批单（未通过保持 private）
    if request.is_public is True and not existing.is_public:
        await assert_approved("action", request.action_id, "publish")

    model = await action_crud_svr.update(
        id=existing.id,
        name=request.name,
        description=request.description,
        version=request.version,
        parameters_schema=request.parameters_schema,
        steps=request.steps,
        tags=request.tags,
        input_vars=input_vars_dicts,
        output_vars=request.output_vars,
        is_public=request.is_public,
        icon_series=request.icon_series,
        icon_id=request.icon_id,
        timeout=request.timeout,
        retry_on_error=request.retry_on_error,
        retry_times=request.retry_times,
        retry_delay=request.retry_delay,
        log_enabled=request.log_enabled,
        log_debug=request.log_debug,
        log_record_params=request.log_record_params,
        log_record_result=request.log_record_result,
        log_record_variables=request.log_record_variables,
        log_record_page_snapshot=request.log_record_page_snapshot,
        log_only_on_error=request.log_only_on_error,
        log_max_payload_length=request.log_max_payload_length,
        log_retention_days=request.log_retention_days,
        auto_sync_upstream=request.auto_sync_upstream,
    )

    if not model:
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在")

    # 采集配置缓存按 action 维度 TTL 缓存，更新后立即失效
    invalidate_cache(request.action_id)

    # 将字典转换回 InputVarDefinition 对象
    input_vars_objs = [
        InputVarDefinition(**var) if isinstance(var, dict) else var
        for var in (model.input_vars or [])
    ]

    # 从多对多关联表加载标签
    tags = await action_crud_svr.get_tags_for_action(model.id)

    # 上游同步状态（更新后重新计算：本地修改标记可能刚被置上）
    sync_flags = await action_crud_svr.attach_upstream_update_flags([model])

    return success_response(
        CompositeActionDetailResponse(
            action_id=model.action_id,
            name=model.name,
            version=model.version,
            content_hash=model.content_hash,
            action_type=model.action_type,
            description=model.description,
            icon_series=model.icon_series,
            icon_id=model.icon_id,
            mid=model.mid,
            parameters_schema=model.parameters_schema,
            steps=_convert_steps(model.steps),
            tags=tags,
            input_vars=input_vars_objs,
            output_vars=model.output_vars,
            is_enabled=model.is_enabled,
            is_public=model.is_public,
            timeout=model.timeout,
            retry_on_error=model.retry_on_error,
            retry_times=model.retry_times,
            retry_delay=model.retry_delay,
            log_enabled=model.log_enabled,
            log_debug=model.log_debug,
            log_record_params=model.log_record_params,
            log_record_result=model.log_record_result,
            log_record_variables=model.log_record_variables,
            log_record_page_snapshot=model.log_record_page_snapshot,
            log_only_on_error=model.log_only_on_error,
            log_max_payload_length=model.log_max_payload_length,
            log_retention_days=model.log_retention_days,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
            **_upstream_sync_kwargs(model, sync_flags.get(model.id)),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )


@router.post(BrowserControlRouterPath.custom_actions_delete, summary="删除自定义操作")
async def delete_custom_action(
    request: CompositeActionDeleteRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """删除自定义操作"""
    action_id = request.action_id
    if not action_id:
        return error_response(400, "缺少操作 action_id")

    model = await action_crud_svr.get_by_action_id(action_id)
    if not model or model.mid != str(auth.mid):
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在或无权限")

    success = await action_crud_svr.delete(model.id)
    if success:
        # 删除后立即失效采集配置缓存
        invalidate_cache(action_id)
        return success_response({"message": "删除成功"})
    else:
        return error_response(500, "删除失败")


@router.post(
    BrowserControlRouterPath.custom_actions_tags, summary="获取用户所有去重标签"
)
async def list_custom_action_tags(
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[str]]:
    """获取当前用户所有自定义操作的去重标签列表"""
    tags = await action_crud_svr.list_tags_by_user(auth.mid)
    return success_response(tags)


@router.post(BrowserControlRouterPath.custom_actions_tags_search, summary="搜索标签")
async def search_custom_action_tags(
    request: TagSearchRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[TagWithCount]]:
    """搜索标签（支持关键字模糊匹配），返回标签名称及其关联操作数量"""
    tags = await action_crud_svr.search_tags_by_user(
        auth.mid, request.keyword, request.filter_type
    )
    return success_response([TagWithCount(**t) for t in tags])


@router.post(
    BrowserControlRouterPath.custom_actions_names_search,
    summary="搜索操作名称（输入联想）",
)
async def search_custom_action_names(
    request: NameSearchRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[str]]:
    """搜索操作名称（用于输入框联想），返回去重的名称列表"""
    names = await action_crud_svr.search_names_by_user(
        auth.mid, request.keyword, request.filter_type
    )
    return success_response(names)


@router.post(
    "/custom_actions/fork",
    summary="Fork 自定义操作（类似 GitHub）",
    response_model=StandardResponse[ActionForkResponse],
)
async def fork_custom_action(
    request: ActionForkRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[ActionForkResponse]:
    """Fork 自定义操作（公开的动作 + 自己的动作都可以 Fork）

    类似 GitHub 的 Fork 功能：把动作（含其依赖树）复制一份到自己的空间，可重命名。
    `action_id` 既可以是公开的社区动作，也可以是自己已拥有的动作（效果等同建副本）。

    Args:
        request: {"action_id": <操作action_id>, "new_name": <新名称（可选）>,
                  "auto_sync_upstream": <是否开启上游自动同步，默认 false>}
    """
    # 获取原操作
    original = await action_crud_svr.get_by_action_id(request.action_id)
    if not original:
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在")

    # 检查是否为公开操作，或者是自己的操作也允许 fork
    if not original.is_public and original.mid != str(auth.mid):
        return error_response(403, "只能 Fork 公开的操作或自己的操作")

    try:
        # 执行 Fork（递归深拷贝所有第三方依赖）
        model = await action_crud_svr.fork(
            id=original.id,
            target_mid=auth.mid,
            new_name=request.new_name,
            auto_sync_upstream=request.auto_sync_upstream,
        )

        if not model:
            return error_response(500, "Fork 失败")

        return success_response(
            ActionForkResponse(
                action_id=model.action_id,
                name=model.name,
                forked_from=original.action_id,
            ),
            msg="Fork 成功，关联动作已独立复制",
        )
    except ValueError as e:
        # ValueError 包含 pydantic 校验失败（响应体字段类型不匹配等），
        # 此前仅静默返回 400，日志里看不到任何痕迹，排查困难
        logger.opt(exception=True).error(
            f"Fork 自定义操作失败 action_id={request.action_id}: {e}"
        )
        return error_response(400, str(e))
    except Exception as e:
        # ActionNotAccessibleException / ActionNotFoundException 等
        if isinstance(e, BizBaseException):
            return error_response(e.code or 400, str(e))
        raise


@router.post(
    "/custom_actions/sync",
    summary="同步自定义操作到上游最新版本",
    response_model=StandardResponse[ActionSyncResponse],
)
async def sync_custom_action(
    request: ActionSyncRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[ActionSyncResponse]:
    """将 Fork 的自定义操作同步到原作者最新版本

    会递归同步整个依赖树：
    - 已有 Fork 副本的子动作会被更新到最新
    - 上游新增的依赖会自动 Fork
    - 上游已删除/私有化的依赖会保留当前副本并在 unavailable 中报告
    """
    # 校验操作归属
    model = await action_crud_svr.get_by_action_id(request.action_id)
    if not model:
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在")
    if str(model.mid) != str(auth.mid):
        return error_response(403, "只能同步自己的操作")

    try:
        result = await action_crud_svr.sync_from_upstream(
            action_id=request.action_id,
            mid=auth.mid,
        )

        msg_parts = []
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
            ActionSyncResponse(
                action_id=result["action_id"],
                updated=result["updated"],
                created=result["created"],
                unavailable=result["unavailable"],
                local_overwritten=bool(result.get("local_overwritten")),
            ),
            msg=msg,
        )
    except ValueError as e:
        logger.opt(exception=True).error(
            f"同步自定义操作失败 action_id={request.action_id}: {e}"
        )
        return error_response(400, str(e))
    except Exception as e:
        if isinstance(e, BizBaseException):
            return error_response(e.code or 400, str(e))
        raise


@router.get("/custom_actions/{id}/forks", summary="获取自定义操作的所有 Fork 版本")
async def get_action_forks(
    id: int,
    skip: int = 0,
    limit: int = 50,
) -> StandardResponse[BasePaginationResp[CompositeActionListItemResponse]]:
    """获取某自定义操作的所有 Fork 版本列表"""
    original = await action_crud_svr.get_by_id(id)
    if not original:
        return error_response(ResponseCode.ACTION_NOT_FOUND, "操作不存在")

    forks = await action_crud_svr.list_forks(id, skip, limit)

    # 批量计算上游同步状态
    sync_flags = await action_crud_svr.attach_upstream_update_flags(forks)

    items = [
        CompositeActionListItemResponse(
            id=f.id,
            action_id=f.action_id,
            name=f.name,
            action_type=f.action_type,
            description=f.description,
            icon_series=f.icon_series,
            icon_id=f.icon_id,
            steps_count=len(f.steps) if f.steps else 0,
            is_enabled=f.is_enabled,
            is_public=f.is_public,
            log_enabled=f.log_enabled,
            log_debug=f.log_debug,
            likes_count=f.likes_count,
            reports_count=f.reports_count,
            is_verified=f.is_verified,
            forks_count=f.forks_count,
            forked_from_id=f.forked_from_id,
            **_upstream_sync_kwargs(f, sync_flags.get(f.id)),
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in forks
    ]

    pagination = BasePaginationResp[CompositeActionListItemResponse](
        page=1, per_page=limit, total=len(items), items=items
    )

    return success_response(pagination)
