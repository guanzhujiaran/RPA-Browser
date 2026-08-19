"""推送消息生产者（兼容薄封装）：将推送请求发布到 message-service 消费的 RabbitMQ 队列。

实际实现已泛化下沉至 `bili_common.core.message_pub.publish_push_message`，
本文件仅保留 RPA-Browser 侧的 `publish_message` 入口，保证存量调用点
（`push_msg.py` 的 `from app.services.message.message_pub import publish_message`）零改动：

- `amqp_url` 复用本服务 `settings.rabbitmq_url`；
- fire-and-forget：只负责发布到 `message.push` 队列，不等待 message-service 消费与
  第三方渠道结果；发布失败上抛，由调用方决定是否兜底。

RPA-Browser 的 per-user 推送配置（PushChannelConfig）会作为消息的 config 字段一并发送，
由 message-service 统一完成实际推送，因此本后端不再直接调用任何推送接口。
"""

from typing import Optional, Union

from bili_common.core.message_pub import publish_push_message
from bili_common.models.push import PushChannelConfig

from app.config import settings

# 兼容导出：与旧实现的 `message_broker` 语义等价（懒连接，首次发布时建立）
# 依赖方如需直接操作 broker，可改用 publish_push_message(amqp_url=settings.rabbitmq_url)
# 或从 bili_common.core.message_pub import _get_broker


async def publish_message(
    title: str,
    content: str,
    push_type: Optional[str] = "text",
    config: Optional[Union[PushChannelConfig, dict]] = None,
) -> None:
    """发布一条推送请求到 message-service（兼容旧签名，行为同 bili-common 公共函数）。"""
    await publish_push_message(
        title=title,
        content=content,
        push_type=push_type,
        config=config,
        amqp_url=settings.rabbitmq_url,
    )
