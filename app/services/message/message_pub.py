"""推送消息生产者：将推送请求发布到 message-service 消费的 RabbitMQ 队列。

RPA-Browser 的 per-user 推送配置（PushChannelConfig）会作为消息的 config 字段一并发送，
由 message-service 统一完成实际推送，因此本后端不再直接调用任何推送接口。
"""

from typing import Optional, Union

from faststream.rabbit import RabbitBroker, RabbitExchange, ExchangeType

from app.config import settings
from loguru import logger
from bili_common.models.push import PushMessagePayload, PushChannelConfig

message_broker = RabbitBroker(settings.rabbitmq_url)
message_exchange = RabbitExchange(
    "message_exchange",
    type=ExchangeType.TOPIC,
    durable=True,
    auto_delete=False,
)


async def publish_message(
    title: str,
    content: str,
    push_type: Optional[str] = "text",
    config: Optional[Union[PushChannelConfig, dict]] = None,
) -> None:
    """发布一条推送请求到 message-service。

    config 优先传 PushChannelConfig（SQLModel）；若调用方只有 dict（例如
    NotificationConfig.model_dump()），则就地构造为 PushChannelConfig。
    """
    payload = PushMessagePayload(
        title=title,
        content=content,
        push_type=push_type,
        config=(
            config
            if isinstance(config, PushChannelConfig)
            else PushChannelConfig(**config)
            if config
            else None
        ),
    )
    try:
        # 懒连接：首次发布时建立连接，之后复用
        if not getattr(message_broker, "_connection", None):
            await message_broker.start()
        # 仅发布到交换机 + routing_key；队列绑定由 be-message-service 维护，
        # 避免本端重复声明把 message_queue 绑定成 message.# 而截获 pptr RPC 请求
        await message_broker.publish(
            message=payload,
            exchange=message_exchange,
            routing_key="message.push",
        )
        logger.debug(f"已发布推送消息到 message 队列: {title}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"发布推送消息到 message-service 失败: {e}")
