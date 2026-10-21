"""站内系统通知 RPC 客户端（RPA-Browser 为客户端）。

调用 be-message-service 的 `message.notify.rpc.publish_notify`
（契约见 `bili_common.rpc.notify`），写入 `msg_notify` 并向目标用户投递站内系统通知。

站内系统通知**无需用户额外配置**：由 be-message 读扩散投递，仅受用户自身
「接收系统通知」（`recv_notify`）开关约束。

作为工作流运行汇报的弱依赖通道：RPC 未连接 / 超时 / 返回非 0 **都不影响工作流执行**
（一律返回 False，不抛异常），但**必须用 warning/error 记录**——通知是用户唯一能感知
「定时任务失败」的渠道，用 debug 记录会让 `INFO` 级日志下彻底静默，
失败排查时完全看不到原因。

RPC 客户端单例（`rpc_client`）由 main.py 的 lifespan 在启动时 connect()。
"""

from bili_common.models.notify import NotifyLevelEnum, NotifyTargetTypeEnum
from bili_common.rpc.base import notify_rpc_routing_key_for
from bili_common.rpc.notify import NotifyRpcMethodName, PublishNotifyParams
from loguru import logger

from app.services.mq.rpc_client import rpc_client

# 一次运行最多发两条（开始 / 结束），属展示性汇报，超时宁短勿长
_PUBLISH_TIMEOUT_SEC = 3.0


async def publish_notify(
    mid: int | str,
    title: str,
    content: str,
    *,
    level: NotifyLevelEnum = NotifyLevelEnum.NORMAL,
    jump_url: str | None = None,
) -> bool:
    """向单个用户发布一条站内系统通知。

    幂等由 be-message 侧按 `(target_value, title)` 保证：同一标题重复调用不会重复
    投递，因此调用方标题需携带运行唯一标识（run_id）区分不同运行。

    Returns:
        True 表示 RPC 调用成功（`code == 0`）；未连接 / 异常 / 非 0 均返回 False。
    """
    if not rpc_client.connected:
        # 未连接属于部署/启动顺序问题：通知会静默丢失，必须告警而不是 debug
        logger.warning(f"站内通知 RPC 未连接，跳过: mid={mid}, title={title}")
        return False

    params = PublishNotifyParams(
        title=title,
        content=content,
        target_type=NotifyTargetTypeEnum.CUSTOM,
        target_value=str(mid),
        level=level,
        jump_url=jump_url,
        publish_now=True,
        creator_mid=0,
    )
    try:
        resp = await rpc_client.call(
            notify_rpc_routing_key_for(NotifyRpcMethodName.PUBLISH_NOTIFY),
            params.model_dump(mode="json"),
            timeout=_PUBLISH_TIMEOUT_SEC,
        )
    except Exception as e:  # noqa: BLE001 - 弱依赖，失败不抛
        # 超时 / 断连 / 序列化异常：工作流继续，但通知没发出去，必须能在日志里看到
        logger.error(
            f"站内通知 RPC 调用失败: mid={mid}, title={title}, "
            f"routing_key={notify_rpc_routing_key_for(NotifyRpcMethodName.PUBLISH_NOTIFY)}, "
            f"error={type(e).__name__}: {e}"
        )
        return False

    if resp.get("code") != 0:
        logger.error(
            f"站内通知 RPC 返回失败码: mid={mid}, title={title}, resp={resp}"
        )
        return False
    return True


__all__ = ["publish_notify"]