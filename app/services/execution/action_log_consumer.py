"""浏览器操作日志的「消费端」：从 RabbitMQ 拉取日志消息并幂等落库。

与 `app/services/mq/rpc_server.py` 一致，本模块刻意使用**普通 `RabbitBroker`
（非 FastAPI 集成）**，因此 `@broker.subscriber` 是正确写法（FastStream 会把消息体
直接 decode 并注入 handler 参数）。

拓扑（定义见 `bili_common.core.log_pub`）：
    交换机 `rpa_log_exchange`（TOPIC / durable）
      └─ 队列 `rpa_action_log_queue`（durable）绑定 `rpa.action.log`

可靠性：
    - durable 队列 + persistent 消息 → broker 重启不丢消息；
    - 落库幂等（`log_id` 唯一）：重复投递 / 崩溃重投不会产生重复记录；
    - 落库失败重试 3 次（指数退避），仍失败写一条 `[LogConsumeFailed]` 兜底记录，
      再失败则丢弃并计数——**绝不让异常冒泡**，避免 FastStream 无限重投刷屏。

生命周期由 `main.py` 的 lifespan 显式 `start()` / `stop()` 管理，
并受 `settings.action_log_use_mq` 开关控制（关闭时本 broker 完全不启动）。
"""

from __future__ import annotations

import asyncio

from faststream.rabbit import RabbitBroker, RabbitQueue
from loguru import logger

from bili_common.core.log_pub import (
    ACTION_LOG_QUEUE_NAME,
    ACTION_LOG_ROUTING_KEY,
    ActionLogMessage,
    action_log_exchange,
)

from app.config import settings
from app.models.database.log.models import ActionLogRecord, ActionLogStatusEnum
from app.services.execution.crud_service import action_log_crud_svr

# 日志消费 broker（独立于 RPC 服务端 broker，普通 RabbitBroker）
broker = RabbitBroker(settings.rabbitmq_url)

_LOG_QUEUE = RabbitQueue(
    ACTION_LOG_QUEUE_NAME,
    durable=True,
    routing_key=ACTION_LOG_ROUTING_KEY,
)

# 落库重试次数与指数退避基数（秒）
_RETRY_TIMES = 3
_RETRY_BASE_DELAY = 0.2

# 消费计数：供观测「重复 / 降级 / 丢弃」情况
_stats = {"consumed": 0, "duplicate": 0, "degraded": 0, "dropped": 0}


def get_log_consumer_stats() -> dict:
    """返回日志消费端计数快照。"""
    return dict(_stats)


@broker.subscriber(queue=_LOG_QUEUE, exchange=action_log_exchange)
async def consume_action_log(message: ActionLogMessage) -> None:
    """消费一条操作日志消息并幂等落库（异常一律在内部消化）。"""
    raw = message.record or {}
    try:
        record = ActionLogRecord.model_validate(raw)
    except Exception as e:  # noqa: BLE001 - 脏消息直接丢弃，不让异常冒泡
        _stats["dropped"] += 1
        logger.warning(
            f"[ActionLogConsumer] 消息体解析失败，丢弃: log_id={raw.get('log_id')} err={e}"
        )
        return

    delay = _RETRY_BASE_DELAY
    last_error = ""
    for attempt in range(1, _RETRY_TIMES + 1):
        try:
            saved = await action_log_crud_svr.create_idempotent(record)
            _stats["consumed"] += 1
            if saved is None:
                _stats["duplicate"] += 1
                logger.debug(f"[ActionLogConsumer] 重复日志已跳过: log_id={record.log_id}")
            return
        except Exception as e:  # noqa: BLE001 - 重试后仍失败则兜底
            last_error = str(e)
            logger.warning(
                f"[ActionLogConsumer] 落库失败(第{attempt}/{_RETRY_TIMES}次): "
                f"log_id={record.log_id} err={e}"
            )
            if attempt < _RETRY_TIMES:
                await asyncio.sleep(delay)
                delay *= 2

    # 重试耗尽 → 写一条兜底失败记录（同样幂等）
    _stats["degraded"] += 1
    record.id = None
    record.status = ActionLogStatusEnum.FAILED
    record.success = False
    record.error_message = f"[LogConsumeFailed] {last_error}"[:2000]
    try:
        await action_log_crud_svr.create_idempotent(record)
    except Exception:  # noqa: BLE001 - 最后兜底仍失败，只能丢弃并计数
        _stats["dropped"] += 1
        logger.error(
            f"[ActionLogConsumer] 兜底落库仍失败，丢弃: log_id={record.log_id}"
        )


async def start_log_consumer() -> None:
    """启动日志消费端（broker.start()，声明队列并开始消费）。"""
    await broker.start()
    logger.info("[ActionLogConsumer] 日志消费端已连接 RabbitMQ")


async def stop_log_consumer() -> None:
    """停止日志消费端。"""
    if broker.running:
        await broker.stop()
    logger.info("[ActionLogConsumer] 日志消费端已断开 RabbitMQ")


__all__ = [
    "broker",
    "consume_action_log",
    "get_log_consumer_stats",
    "start_log_consumer",
    "stop_log_consumer",
]