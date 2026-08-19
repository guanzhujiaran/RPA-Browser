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
from sqlalchemy import select

from bili_common.models.response import StandardResponse, error_response, success_response
from bili_common.rpc.base import rpa_rpc_routing_key_for
from bili_common.rpc.rpa import (
    GetResourceDetailParams,
    GetResourceDetailResult,
    RpaRpcMethodName,
    ResourceDetail,
)
from bili_common.rpc.safe import rpc_safe

from app.config import settings
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
]
