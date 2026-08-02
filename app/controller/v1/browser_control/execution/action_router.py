"""
Action 管理路由

提供系统预注册 Action 和用户复合 Action（Composite Action）的 API
"""
from typing import Dict, List
import uuid
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from fastapi import Depends
from app.services.execution.crud_service import action_crud_svr
from app.services.execution.crud_service.action_crud import ActionCrudService
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
    InputVarDefinition,
    TagSearchRequest,
    TagWithCount,
    NameSearchRequest,
)
from app.models.execution.action_params import ActionMetadataResponse
from app.models.base.base_sqlmodel import BasePaginationResp
from ..base import new_action_router

router = new_action_router()


def _convert_steps(steps: list) -> list[Dict]:
    """将 WorkflowStep 列表转换为 dict 列表"""
    return [step.model_dump() if hasattr(step, "model_dump") else step for step in steps]


# ============ 系统预注册操作（只读，全 POST） ============


@router.post(BrowserControlRouterPath.actions_registered, summary="获取系统预注册操作列表")
async def list_registered_actions() -> StandardResponse[List[ActionMetadataResponse]]:
    """获取系统预注册操作列表（公开，只读）

    返回精简版 Action 元数据，仅包含 action_id 和 json_schema
    """
    from app.models.execution.action_params import BuiltinActionType
    response_actions = [
        ActionMetadataResponse(
            action_id=action_type.value,
            action_type=action_type,
            name=action_type.nameDisplay,
            json_schema=action_type.metadata.json_schema or {},
        )
        for action_type in BuiltinActionType
    ]
    return success_response(response_actions)


# ============ 自定义操作管理（用户自定义 Action） ============


@router.post(BrowserControlRouterPath.custom_actions_create, summary="创建自定义操作")
async def create_custom_action(
    request: CompositeActionCreateRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CompositeActionDetailResponse]:
    """创建用户自定义操作

    用户可以基于系统预注册的操作或插件组合创建自己的操作。
    action_id 由系统自动生成（格式：ca_xxx），用户仅需提供显示名称 name。
    """
    # 生成唯一的 action_id（格式：ca_xxx）
    action_id = f"ca_{uuid.uuid4().hex[:12]}"

    # 将 InputVarDefinition 对象转换为字典以便正确序列化
    input_vars_dicts = [
        var.model_dump() if hasattr(var, "model_dump") else dict(var)
        for var in (request.input_vars or [])
    ]

    model = await action_crud_svr.create(
        action_id=action_id,
        name=request.name,
        mid=auth.mid,
        description=request.description,
        steps=request.steps,
        tags=request.tags or [],
        input_vars=input_vars_dicts,
        output_vars=request.output_vars,
        timeout=request.timeout,
        retry_on_error=request.retry_on_error,
        retry_times=request.retry_times,
        retry_delay=request.retry_delay,
        is_public=request.is_public,
        log_enabled=request.log_enabled,
        log_record_params=request.log_record_params,
        log_record_result=request.log_record_result,
        log_record_variables=request.log_record_variables,
        log_only_on_error=request.log_only_on_error,
        log_max_payload_length=request.log_max_payload_length,
        log_retention_days=request.log_retention_days,
    )

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
            action_type=model.action_type,
            description=model.description,
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
            log_record_params=model.log_record_params,
            log_record_result=model.log_record_result,
            log_record_variables=model.log_record_variables,
            log_only_on_error=model.log_only_on_error,
            log_max_payload_length=model.log_max_payload_length,
            log_retention_days=model.log_retention_days,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
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

    items = [
        CompositeActionListItemResponse(
            action_id=model.action_id,
            name=model.name,
            action_type=model.action_type,
            description=model.description,
            steps_count=len(model.steps) if model.steps else 0,
            tags=tags_map.get(model.id, []),
            is_enabled=model.is_enabled,
            is_public=model.is_public,
            log_enabled=model.log_enabled,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
        for model in models
    ]

    # 构建分页响应
    pagination = BasePaginationResp[CompositeActionListItemResponse](
        page=request.page,
        per_page=request.per_page,
        total=total,
        items=items
    )

    return success_response(pagination)


@router.post(BrowserControlRouterPath.custom_actions_get, summary="获取自定义操作详情")
async def get_custom_action(
    request: CompositeActionGetRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[CompositeActionDetailResponse]:
    """获取自定义操作详情，steps 中引用的 ca_ 操作会自动附加完整信息并校验权限"""
    action_id = request.action_id
    if not action_id:
        return error_response(400, "缺少操作action_id")

    model = await action_crud_svr.get_by_action_id(str(action_id))
    if not model:
        return error_response(404, "操作不存在")

    if model.mid != str(auth.mid) and not model.is_public:
        return error_response(404, "操作不存在")

    # 将字典转换回 InputVarDefinition 对象
    input_vars_objs = [
        InputVarDefinition(**var) if isinstance(var, dict) else var
        for var in (model.input_vars or [])
    ]

    # 解析 steps 并校验所有引用的 ca_ 操作的权限，同时获取完整详情
    steps_data = _convert_steps(model.steps)
    try:
        ca_ids = ActionCrudService._collect_ca_action_ids(steps_data)
        if ca_ids:
            detail_map = await action_crud_svr.get_validated_action_details(ca_ids, auth.mid)
            ActionCrudService._annotate_steps_with_action_details(steps_data, detail_map)
    except Exception as e:
        return error_response(getattr(e, "code", 403), str(e.msg) if hasattr(e, "msg") else str(e))

    # 从多对多关联表加载标签
    tags = await action_crud_svr.get_tags_for_action(model.id)

    return success_response(
        CompositeActionDetailResponse(
            action_id=model.action_id,
            name=model.name,
            version=model.version,
            action_type=model.action_type,
            description=model.description,
            mid=model.mid,
            parameters_schema=model.parameters_schema,
            steps=steps_data,
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
            log_record_params=model.log_record_params,
            log_record_result=model.log_record_result,
            log_record_variables=model.log_record_variables,
            log_only_on_error=model.log_only_on_error,
            log_max_payload_length=model.log_max_payload_length,
            log_retention_days=model.log_retention_days,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
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
        return error_response(404, "操作不存在或无权限")

    # 将 InputVarDefinition 对象转换为字典以便正确序列化
    input_vars_dicts = None
    if request.input_vars is not None:
        input_vars_dicts = [
            var.model_dump() if hasattr(var, "model_dump") else dict(var)
            for var in request.input_vars
        ]

    model = await action_crud_svr.update(
        id=existing.id,
        name=request.name,
        description=request.description,
        steps=request.steps,
        tags=request.tags,
        input_vars=input_vars_dicts,
        output_vars=request.output_vars,
        timeout=request.timeout,
        retry_on_error=request.retry_on_error,
        retry_times=request.retry_times,
        retry_delay=request.retry_delay,
        log_enabled=request.log_enabled,
        log_record_params=request.log_record_params,
        log_record_result=request.log_record_result,
        log_record_variables=request.log_record_variables,
        log_only_on_error=request.log_only_on_error,
        log_max_payload_length=request.log_max_payload_length,
        log_retention_days=request.log_retention_days,
    )

    if not model:
        return error_response(404, "操作不存在")

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
            action_type=model.action_type,
            description=model.description,
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
            log_record_params=model.log_record_params,
            log_record_result=model.log_record_result,
            log_record_variables=model.log_record_variables,
            log_only_on_error=model.log_only_on_error,
            log_max_payload_length=model.log_max_payload_length,
            log_retention_days=model.log_retention_days,
            likes_count=model.likes_count,
            reports_count=model.reports_count,
            is_verified=model.is_verified,
            forks_count=model.forks_count,
            forked_from_id=model.forked_from_id,
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

    model = await action_crud_svr.get_by_action_id(str(action_id))
    if not model or model.mid != str(auth.mid):
        return error_response(404, "操作不存在或无权限")

    success = await action_crud_svr.delete(model.id)
    if success:
        return success_response({"message": "删除成功"})
    else:
        return error_response(500, "删除失败")


@router.post(BrowserControlRouterPath.custom_actions_tags, summary="获取用户所有去重标签")
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


@router.post(BrowserControlRouterPath.custom_actions_names_search, summary="搜索操作名称（输入联想）")
async def search_custom_action_names(
    request: NameSearchRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[List[str]]:
    """搜索操作名称（用于输入框联想），返回去重的名称列表"""
    names = await action_crud_svr.search_names_by_user(
        auth.mid, request.keyword, request.filter_type
    )
    return success_response(names)


@router.post("/custom_actions/fork", summary="Fork 自定义操作（类似 GitHub）", response_model=StandardResponse[ActionForkResponse])
async def fork_custom_action(
    request: ActionForkRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[ActionForkResponse]:
    """Fork 自定义操作（仅允许 Fork 公开的操作）

    类似 GitHub 的 Fork 功能，允许用户复制公开的社区操作到自己的空间，并可以选择重命名。

    Args:
        request: {"action_id": <操作action_id>, "new_name": <新名称（可选）>}
    """
    # 获取原操作
    original = await action_crud_svr.get_by_action_id(request.action_id)
    if not original:
        return error_response(404, "操作不存在")

    # 检查是否为公开操作，或者是自己的操作也允许 fork
    if not original.is_public and original.mid != str(auth.mid):
        return error_response(403, "只能 Fork 公开的操作或自己的操作")

    try:
        # 执行 Fork
        model = await action_crud_svr.fork(
            id=original.id,
            target_mid=auth.mid,
            new_name=request.new_name
        )

        if not model:
            return error_response(500, "Fork 失败")

        return success_response(
            ActionForkResponse(
                action_id=model.action_id,
                name=model.name,
                forked_from=original.name,
            ),
            msg="Fork 成功"
        )
    except ValueError as e:
        return error_response(400, str(e))


@router.get("/custom_actions/{id}/forks", summary="获取自定义操作的所有 Fork 版本")
async def get_action_forks(
    id: int,
    skip: int = 0,
    limit: int = 50,
) -> StandardResponse[BasePaginationResp[CompositeActionListItemResponse]]:
    """获取某自定义操作的所有 Fork 版本列表"""
    original = await action_crud_svr.get_by_id(id)
    if not original:
        return error_response(404, "操作不存在")

    forks = await action_crud_svr.list_forks(id, skip, limit)

    items = [
        CompositeActionListItemResponse(
            action_id=f.action_id,
            name=f.name,
            action_type=f.action_type,
            description=f.description,
            steps_count=len(f.steps) if f.steps else 0,
            is_enabled=f.is_enabled,
            is_public=f.is_public,
            log_enabled=f.log_enabled,
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

    pagination = BasePaginationResp[CompositeActionListItemResponse](
        page=1,
        per_page=limit,
        total=len(items),
        items=items
    )

    return success_response(pagination)
