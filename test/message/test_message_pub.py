"""
测试站外推送 MQ 公共发布函数（fire-and-forget）。

覆盖：
- publish_push_message 的 payload 构造（title/content/push_type/config）
- config 兼容：PushChannelConfig 原样透传、dict 就地转 PushChannelConfig、None 允许
- 只发布到 message_exchange 的 message.push 路由（不声明队列）
- RPA-Browser 薄封装 publish_message 透传 settings.rabbitmq_url

使用 unittest.mock 模拟 broker.start() 与 broker.publish()，不需要真实 RabbitMQ。
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from bili_common.core import message_pub as mp
from bili_common.models.push import PushChannelConfig
from app.services.message import message_pub as rpa_message_pub
from app.services.message.message_pub import publish_message


@pytest.fixture(autouse=True)
def _reset_brokers():
    """每个用例前清空 broker 缓存，避免跨用例复用连接状态。"""
    mp._brokers.clear()
    yield
    mp._brokers.clear()


def test_normalize_config_none():
    """config=None 时返回 None。"""
    assert mp._normalize_config(None) is None


def test_normalize_config_passthrough():
    """config 已是 PushChannelConfig 时原样透传。"""
    cfg = PushChannelConfig(bark_push="https://x", hitokoto=False)
    assert mp._normalize_config(cfg) is cfg


def test_normalize_config_from_dict():
    """config 为 dict 时就地构造 PushChannelConfig。"""
    cfg = mp._normalize_config({"bark_push": "https://x", "hitokoto": False})
    assert isinstance(cfg, PushChannelConfig)
    assert cfg.bark_push == "https://x"
    assert cfg.hitokoto is False


def test_publish_payload_and_routing():
    """publish_push_message 构造正确 payload 并发布到 message.push 路由。"""
    broker = AsyncMock()
    broker._connection = None  # 触发懒连接 start()
    with patch.object(mp, "_get_broker", return_value=broker) as mock_get:
        asyncio.run(
            mp.publish_push_message(
                title="[i] 测试",
                content="正文",
                push_type="markdown",
                config={"bark_push": "https://x"},
                amqp_url="amqp://guest:guest@localhost:5672/",
            )
        )

    mock_get.assert_called_once_with("amqp://guest:guest@localhost:5672/")
    broker.start.assert_awaited_once()
    broker.publish.assert_awaited_once()
    call_kwargs = broker.publish.await_args.kwargs
    assert call_kwargs["routing_key"] == "message.push"
    assert call_kwargs["exchange"].name == "message_exchange"
    payload = call_kwargs["message"]
    assert payload.title == "[i] 测试"
    assert payload.content == "正文"
    assert payload.push_type == "markdown"
    assert isinstance(payload.config, PushChannelConfig)
    assert payload.config.bark_push == "https://x"


def test_publish_reuses_connection():
    """已有连接时不重复 start()。"""
    broker = AsyncMock()
    broker._connection = object()  # 已连接
    with patch.object(mp, "_get_broker", return_value=broker):
        asyncio.run(
            mp.publish_push_message(
                title="t", content="c", amqp_url="amqp://guest:guest@localhost:5672/"
            )
        )
    broker.start.assert_not_awaited()
    broker.publish.assert_awaited_once()


def test_rpa_wrapper_delegates():
    """RPA-Browser 薄封装 publish_message 透传 settings.rabbitmq_url。

    注意：薄封装模块顶部用 `from ... import publish_push_message` 绑定了旧引用，
    因此需 patch 薄封装模块命名空间里的名字，而不是 bili_common 模块。
    """
    with patch.object(rpa_message_pub, "publish_push_message", new=AsyncMock()) as mock_pub:
        asyncio.run(publish_message("t", "c", config={"bark_push": "https://x"}))
        mock_pub.assert_awaited_once()
        kwargs = mock_pub.await_args.kwargs
        assert kwargs["title"] == "t"
        assert kwargs["content"] == "c"
        # amqp_url 来自 settings.rabbitmq_url（非空即认为透传成功）
        assert kwargs["amqp_url"]
        assert isinstance(kwargs["config"], dict)


def test_publish_failure_propagates():
    """发布失败不静默：异常上抛（符合「不静默、不吞错」约定）。"""
    broker = AsyncMock()
    broker._connection = None
    broker.publish.side_effect = RuntimeError("mq down")
    with patch.object(mp, "_get_broker", return_value=broker):
        with pytest.raises(RuntimeError, match="mq down"):
            asyncio.run(
                mp.publish_push_message(
                    title="t", content="c", amqp_url="amqp://guest:guest@localhost:5672/"
                )
            )
