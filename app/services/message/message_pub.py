"""推送消息生产者：将推送请求发布到 message-service 消费的 RabbitMQ 队列。

RPA-Browser 的 per-user 推送配置（PushChannelConfig）会作为消息的 config 字段一并发送，
由 message-service 统一完成实际推送，因此本后端不再直接调用任何推送接口。
"""

from typing import Optional

from faststream.rabbit import RabbitBroker, RabbitExchange, ExchangeType, RabbitQueue

from app.config import settings
from loguru import logger

message_broker = RabbitBroker(settings.rabbitmq_url)
message_exchange = RabbitExchange(
    "message_exchange",
    type=ExchangeType.TOPIC,
    durable=True,
    auto_delete=False,
)
message_queue = RabbitQueue(
    "message_queue",
    routing_key="message.#",
    durable=True,
)


async def publish_message(
    title: str,
    content: str,
    push_type: Optional[str] = "text",
    config: Optional[dict] = None,
) -> None:
    """发布一条推送请求到 message-service。"""
    payload = {
        "title": title,
        "content": content,
        "push_type": push_type,
        "config": config,
    }
    try:
        # 懒连接：首次发布时建立连接，之后复用
        if not getattr(message_broker, "_connection", None):
            await message_broker.start()
        await message_broker.publish(
            message=payload,
            exchange=message_exchange,
            routing_key="message.push",
            queue=message_queue,
        )
        logger.debug(f"已发布推送消息到 message 队列: {title}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"发布推送消息到 message-service 失败: {e}")
