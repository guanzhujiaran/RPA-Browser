"""RPA 资源 RPC 服务端（FastStream RabbitRouter，2.18.0 新增）。

RPA-Browser 作为 RPC 服务端，暴露 `get_resource_detail` 方法，供 be-message
（RPC 客户端）按 `message.rpa.rpc.get_resource_detail` 同步调用，获取 RPA
资源（action / workflow / plugin / browser）详情，随互动状态一并返回前端。

路由键前缀 `message.rpa.rpc` 见 `bili_common.rpc.base`；契约（方法名 / 参数 /
响应模型）见 `bili_common.rpc.rpa`。

消息处理流程（与 be-message 的 message.pptr.rpc 对齐）：
1. FastStream 接收 RabbitMQ 消息，自动把 body JSON validate 为 params_model
2. handler 返回 `StandardResponse{code, msg, data}`，FastStream 自动序列化发送到 reply_to
3. 异常在 RPC 边界由 `rpc_safe` 翻译成 `error_response` 回包，避免客户端超时

生命周期：本模块定义 `router`（RabbitRouter），由 main.py 的 lifespan 显式
`start()` / `stop()` 管理（RPA 非 FastStream FastAPI 应用，不走 include_router）。
"""

from faststream.rabbit import RabbitBroker
from loguru import logger
from sqlalchemy import func, select

from datetime import datetime

from bili_common.models.response import StandardResponse, error_response, success_response
from bili_common.rpc.base import rpa_rpc_routing_key_for
from bili_common.rpc.rpa import (
    AttachTagParams,
    AttachTagResult,
    CreateTagParams,
    CreateTagResult,
    DetachTagParams,
    DetachTagResult,
    GetResourceDetailParams,
    GetResourceDetailResult,
    HideResourceParams,
    HideResourceResult,
    ListTagsByTargetParams,
    ListTagsByTargetResult,
    ListTagsParams,
    ListTagsResult,
    ResourceDetail,
    ReviewResourceParams,
    ReviewResourceResult,
    RpaRpcMethodName,
    TagItemRpc,
)
from bili_common.rpc.safe import rpc_safe

from app.config import settings
from app.models.database.admin.models import ApprovalRequest, ResourceTag, ResourceTagRel
from app.models.database.browser.info import UserBrowserInfo
from app.models.database.workflow.models import (
    CompositeActionModel,
    UserPlugin,
    UserWorkflow,
)
from app.utils.depends.session_manager import DatabaseSessionManager

# RPA RPC 服务端 broker（RabbitBroker，默认 exchange，routing_key 即队列名）
broker = RabbitBroker(settings.rabbitmq_url)


# ---------------------------------------------------------------------------
# 资源详情查询（按 bizType 分派）
# ---------------------------------------------------------------------------


async def _load_resource(biz_type: str, biz_id: int):
    """按 bizType 加载资源实体（None=类型不支持或资源不存在）。"""
    async with DatabaseSessionManager.async_session() as session:
        if biz_type == "rpa_action":
            r = await session.exec(select(CompositeActionModel).where(CompositeActionModel.id == biz_id))
            return r.first()
        if biz_type == "rpa_workflow":
            r = await session.exec(select(UserWorkflow).where(UserWorkflow.id == biz_id))
            return r.first()
        if biz_type == "rpa_plugin":
            r = await session.exec(select(UserPlugin).where(UserPlugin.id == biz_id))
            return r.first()
        if biz_type == "rpa_browser":
            r = await session.exec(select(UserBrowserInfo).where(UserBrowserInfo.browser_id == biz_id))
            return r.first()
        if biz_type == "rpa_tag":
            r = await session.exec(select(ResourceTag).where(ResourceTag.id == biz_id))
            return r.first()
        return None


def _jump_url(biz_type: str, biz_id: int) -> str:
    """按 bizType 生成前端落地页跳转地址。"""
    if biz_type == "rpa_action":
        return "/app/rpa-browser/actions"
    if biz_type == "rpa_workflow":
        return "/app/rpa-browser/workflows"
    if biz_type == "rpa_plugin":
        return "/app/rpa-browser/plugins"
    if biz_type == "rpa_browser":
        return f"/app/rpa-browser/stream/{biz_id}"
    if biz_type == "rpa_tag":
        return "/app/admin/rpa/tag"
    return ""


def _to_detail(biz_type: str, biz_id: int, resource) -> ResourceDetail:
    """把资源实体映射为通用 ResourceDetail。"""
    if resource is None:
        return ResourceDetail(bizType=biz_type, bizId=biz_id)
    # action / workflow / plugin 走 CommunityResourceBase（name + original_mid）
    name = getattr(resource, "name", None) or ""
    author = getattr(resource, "original_mid", None) or getattr(resource, "mid", None)
    # browser 用 custom_name，作者取 mid
    if biz_type == "rpa_browser":
        name = getattr(resource, "custom_name", None) or getattr(resource, "name", None) or ""
        author = getattr(resource, "mid", None)
    # tag 无 original_mid/mid，作者取 created_by（供 be-message 驳回通知）
    if biz_type == "rpa_tag":
        author = getattr(resource, "created_by", None)
    return ResourceDetail(
        bizType=biz_type,
        bizId=biz_id,
        name=name,
        authorMid=str(author) if author is not None else None,
        jumpUrl=_jump_url(biz_type, biz_id),
    )


# ---------------------------------------------------------------------------
# RPC handler
# ---------------------------------------------------------------------------


@broker.subscriber(rpa_rpc_routing_key_for(RpaRpcMethodName.GET_RESOURCE_DETAIL))
@rpc_safe
async def rpc_get_resource_detail(
    params: GetResourceDetailParams,
) -> StandardResponse:
    """获取 RPA 资源详情（get_resource_detail）。

    Args:
        params: GetResourceDetailParams{bizType, bizId}

    Returns:
        StandardResponse data=GetResourceDetailResult{detail}
        资源不存在时 data.detail=None（弱依赖，不抛错）。
    """
    logger.info(
        f"[RpaRpcServer] 收到 get_resource_detail: bizType={params.bizType} bizId={params.bizId}"
    )
    resource = await _load_resource(params.bizType, params.bizId)
    detail = _to_detail(params.bizType, params.bizId, resource)
    return success_response(
        data=GetResourceDetailResult(detail=detail),
        msg="success" if resource is not None else "resource not found",
    )


@broker.subscriber(rpa_rpc_routing_key_for(RpaRpcMethodName.HIDE_RESOURCE))
@rpc_safe
async def rpc_hide_resource(
    params: HideResourceParams,
) -> StandardResponse:
    """举报处置：下架 RPA 本地资源（hide_resource，2.39.0）。

    Args:
        params: HideResourceParams{bizType, bizId, operatorMid, reason}

    说明：
        - lottery 归属 be-bilibili-crawler，本服务端不处理，返回 success=False；
        - rpa_action / rpa_workflow / rpa_plugin：置 ``is_public=False``，退出社区公开；
        - rpa_browser 为运行态浏览器实例（无 is_public 公开语义），不支持下架。

    Returns:
        StandardResponse data=HideResourceResult{success, message}
    """
    biz_type = params.bizType
    if biz_type not in ("rpa_action", "rpa_workflow", "rpa_plugin"):
        return success_response(
            data=HideResourceResult(
                success=False,
                message=f"hide_resource 不处理类型 {biz_type}（lottery 归属 be-bilibili-crawler）",
            )
        )
    model = {
        "rpa_action": CompositeActionModel,
        "rpa_workflow": UserWorkflow,
        "rpa_plugin": UserPlugin,
    }[biz_type]
    async with DatabaseSessionManager.async_session() as session:
        row = await session.exec(select(model).where(model.id == params.bizId))
        resource = row.first()
        if resource is None:
            return success_response(
                data=HideResourceResult(success=False, message="资源不存在")
            )
        resource.is_public = False
        await session.commit()
    logger.info(
        f"[RpaRpcServer] hide_resource 已下架: bizType={biz_type} bizId={params.bizId} "
        f"operatorMid={params.operatorMid} reason={params.reason}"
    )
    return success_response(data=HideResourceResult(success=True))


# ---------------------------------------------------------------------------
# RPA 资源审核（review_resource）：把「发布到社区审批单」置通过/驳回
# ---------------------------------------------------------------------------

# bizType -> (资源模型, 资源业务 id 字段, 审批单 resource_type)
_REVIEW_RESOURCE_MAP: dict[str, tuple[type, str, str]] = {
    "rpa_action": (CompositeActionModel, "action_id", "action"),
    "rpa_workflow": (UserWorkflow, "workflow_id", "workflow"),
    "rpa_plugin": (UserPlugin, "plugin_id", "plugin"),
}


@broker.subscriber(rpa_rpc_routing_key_for(RpaRpcMethodName.REVIEW_RESOURCE))
@rpc_safe
async def rpc_review_resource(
    params: ReviewResourceParams,
) -> StandardResponse:
    """审核 RPA 资源：对其「发布到社区审批单(rpa_approval, action=publish)」置通过/驳回。

    Args:
        params: ReviewResourceParams{bizType, bizId, decision, operatorMid, note}

    说明：
        - 仅处理 rpa_action / rpa_workflow / rpa_plugin；lottery / rpa_browser 不处理。
        - 按 bizId(int 表主键) 查资源取业务 id(action_id / workflow_id / plugin_id)，
          再匹配 rpa_approval 中 (resource_type, resource_id=业务id, action=publish,
          status=pending) 的审批单（多条取 id 最新一条），置 decision。
        - 只改审批单状态，不动资源 is_public（公开/私有保持现状）。

    Returns:
        StandardResponse data=ReviewResourceResult{success, message, approvalId}
    """
    biz_type = params.bizType
    if params.decision not in ("approved", "rejected"):
        return success_response(
            data=ReviewResourceResult(success=False, message="decision 必须为 approved / rejected")
        )
    # rpa_tag：标签不走 rpa_approval 发布审批单，直接置审核状态 + 上架时间
    if biz_type == "rpa_tag":
        async with DatabaseSessionManager.async_session() as session:
            tag = (
                await session.exec(select(ResourceTag).where(ResourceTag.id == params.bizId))
            ).first()
            if tag is None:
                return success_response(
                    data=ReviewResourceResult(success=False, message="标签不存在")
                )
            tag.audit_status = "normal" if params.decision == "approved" else "rejected"
            tag.pub_time = datetime.now() if params.decision == "approved" else None
            await session.commit()
        logger.info(
            f"[RpaRpcServer] review_resource {params.decision}: bizType=rpa_tag "
            f"bizId={params.bizId} operatorMid={params.operatorMid}"
        )
        return success_response(data=ReviewResourceResult(success=True))
    mapping = _REVIEW_RESOURCE_MAP.get(biz_type)
    if mapping is None:
        return success_response(
            data=ReviewResourceResult(
                success=False,
                message=f"review_resource 不处理类型 {biz_type}",
            )
        )
    model, biz_id_field, resource_type = mapping
    async with DatabaseSessionManager.async_session() as session:
        resource = (
            await session.exec(select(model).where(model.id == params.bizId))
        ).first()
        if resource is None:
            return success_response(
                data=ReviewResourceResult(success=False, message="资源不存在")
            )
        biz_resource_id = str(getattr(resource, biz_id_field))
        # 取该资源发布到社区的待审单（id 倒序取最新一条）
        approval = (
            await session.exec(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.resource_type == resource_type,
                    ApprovalRequest.resource_id == biz_resource_id,
                    ApprovalRequest.action == "publish",
                    ApprovalRequest.status == "pending",
                )
                .order_by(ApprovalRequest.id.desc())
            )
        ).first()
        if approval is None:
            return success_response(
                data=ReviewResourceResult(
                    success=False,
                    message="未找到该资源的待审核发布审批单",
                )
            )
        approval.status = params.decision
        approval.reviewer_mid = params.operatorMid
        approval.review_note = params.note or approval.review_note
        approval.reviewed_at = datetime.now()
        await session.commit()
        await session.refresh(approval)
    logger.info(
        f"[RpaRpcServer] review_resource {params.decision}: bizType={biz_type} "
        f"bizId={params.bizId} resource_id={biz_resource_id} approvalId={approval.id} "
        f"operatorMid={params.operatorMid}"
    )
    return success_response(
        data=ReviewResourceResult(success=True, approvalId=approval.id)
    )


# ---------------------------------------------------------------------------
# rpa_tag 全托管回调（2.49.0）：be-message 编排、RPA 落库
# ---------------------------------------------------------------------------


def _to_tag_item_rpc(t: ResourceTag) -> TagItemRpc:
    return TagItemRpc(
        id=t.id,
        name=t.name,
        color=t.color,
        createdBy=t.created_by,
        auditStatus=t.audit_status,
        pubTime=t.pub_time,
        createdAt=t.created_at,
    )


@broker.subscriber(rpa_rpc_routing_key_for(RpaRpcMethodName.CREATE_TAG))
@rpc_safe
async def rpc_create_tag(
    params: CreateTagParams,
) -> StandardResponse:
    """创建资源标签（create_tag）→ 落 `auditing`（be-message 编排）。"""
    async with DatabaseSessionManager.async_session() as session:
        name = (params.name or "").strip()
        if not name:
            return success_response(data=CreateTagResult(success=False, message="标签名不能为空"))
        existing = (
            await session.exec(select(ResourceTag).where(ResourceTag.name == name))
        ).first()
        if existing is not None:
            return success_response(data=CreateTagResult(success=False, message="标签名称已存在"))
        tag = ResourceTag(
            name=name,
            color=params.color or "#409EFF",
            created_by=params.createdMid,
            audit_status="auditing",
            pub_time=None,
        )
        session.add(tag)
        await session.commit()
        await session.refresh(tag)
    logger.info(f"[RpaRpcServer] create_tag: id={tag.id} name={name} createdMid={params.createdMid}")
    return success_response(data=CreateTagResult(success=True, id=tag.id), msg="标签已提交，待审核")


@broker.subscriber(rpa_rpc_routing_key_for(RpaRpcMethodName.ATTACH_TAG))
@rpc_safe
async def rpc_attach_tag(
    params: AttachTagParams,
) -> StandardResponse:
    """为资源关联标签（attach_tag）；仅可关联 `normal` 标签。"""
    async with DatabaseSessionManager.async_session() as session:
        tag = (
            await session.exec(select(ResourceTag).where(ResourceTag.id == params.tagId))
        ).first()
        if tag is None:
            return success_response(data=AttachTagResult(success=False, message="标签不存在"))
        if tag.audit_status != "normal":
            return success_response(
                data=AttachTagResult(success=False, message="该标签尚未审核通过，暂不可关联")
            )
        existing = (
            await session.exec(
                select(ResourceTagRel).where(
                    (ResourceTagRel.tag_id == params.tagId)
                    & (ResourceTagRel.target_type == params.targetType)
                    & (ResourceTagRel.target_id == params.targetId)
                )
            )
        ).first()
        if existing is not None:
            return success_response(
                data=AttachTagResult(success=True, alreadyExist=True), msg="标签已关联"
            )
        rel = ResourceTagRel(
            tag_id=params.tagId,
            target_type=params.targetType,
            target_id=params.targetId,
            created_by=params.createdMid,
        )
        session.add(rel)
        await session.commit()
    logger.info(f"[RpaRpcServer] attach_tag: tagId={params.tagId} target={params.targetType}/{params.targetId}")
    return success_response(data=AttachTagResult(success=True), msg="已关联标签")


@broker.subscriber(rpa_rpc_routing_key_for(RpaRpcMethodName.DETACH_TAG))
@rpc_safe
async def rpc_detach_tag(
    params: DetachTagParams,
) -> StandardResponse:
    """移除资源上的标签（detach_tag）。"""
    async with DatabaseSessionManager.async_session() as session:
        rel = (
            await session.exec(
                select(ResourceTagRel).where(
                    (ResourceTagRel.tag_id == params.tagId)
                    & (ResourceTagRel.target_type == params.targetType)
                    & (ResourceTagRel.target_id == params.targetId)
                )
            )
        ).first()
        if rel is None:
            return success_response(data=DetachTagResult(success=False, message="关联不存在"))
        await session.delete(rel)
        await session.commit()
    logger.info(f"[RpaRpcServer] detach_tag: tagId={params.tagId} target={params.targetType}/{params.targetId}")
    return success_response(data=DetachTagResult(success=True), msg="已移除标签")


@broker.subscriber(rpa_rpc_routing_key_for(RpaRpcMethodName.LIST_TAGS))
@rpc_safe
async def rpc_list_tags(
    params: ListTagsParams,
) -> StandardResponse:
    """列出标签（list_tags）。`auditStatus` 为 None 时默认仅 `normal`；'all' 表示全部（管理端）。"""
    status_filter = (params.auditStatus or "normal").strip()
    page = max(params.page, 1)
    per_page = max(1, min(params.perPage, 200))
    async with DatabaseSessionManager.async_session() as session:
        base_stmt = select(ResourceTag)
        count_stmt = select(func.count()).select_from(ResourceTag)
        if status_filter != "all":
            base_stmt = base_stmt.where(ResourceTag.audit_status == status_filter)
            count_stmt = count_stmt.where(ResourceTag.audit_status == status_filter)
        total_row = (await session.exec(count_stmt)).first()
        total = int(total_row[0]) if total_row is not None else 0
        rows = (
            await session.exec(
                base_stmt.order_by(ResourceTag.id.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        ).all()
    return success_response(
        data=ListTagsResult(total=total, items=[_to_tag_item_rpc(t) for t in rows])
    )


@broker.subscriber(rpa_rpc_routing_key_for(RpaRpcMethodName.LIST_TAGS_BY_TARGET))
@rpc_safe
async def rpc_list_tags_by_target(
    params: ListTagsByTargetParams,
) -> StandardResponse:
    """查询某资源关联的标签（list_tags_by_target）；仅返回 `normal`。"""
    async with DatabaseSessionManager.async_session() as session:
        rows = (
            await session.exec(
                select(ResourceTag)
                .join(ResourceTagRel, ResourceTagRel.tag_id == ResourceTag.id)
                .where(
                    (ResourceTagRel.target_type == params.targetType)
                    & (ResourceTagRel.target_id == params.targetId)
                    & (ResourceTag.audit_status == "normal")
                )
            )
        ).all()
    return success_response(
        data=ListTagsByTargetResult(items=[_to_tag_item_rpc(t) for t in rows])
    )


# ---------------------------------------------------------------------------
# 生命周期（供 main.py lifespan 调用）
# ---------------------------------------------------------------------------


async def start_rpc_server() -> None:
    """启动 RPA RPC 服务端（broker.start()）。"""
    await broker.start()
    logger.info("[RpaRpcServer] RPC 服务端已连接 RabbitMQ")


async def stop_rpc_server() -> None:
    """停止 RPA RPC 服务端。"""
    if broker.running:
        await broker.stop()
    logger.info("[RpaRpcServer] RPC 服务端已断开 RabbitMQ")


__all__ = [
    "broker",
    "start_rpc_server",
    "stop_rpc_server",
    "rpc_get_resource_detail",
    "rpc_hide_resource",
    "rpc_review_resource",
    "rpc_create_tag",
    "rpc_attach_tag",
    "rpc_detach_tag",
    "rpc_list_tags",
    "rpc_list_tags_by_target",
]
