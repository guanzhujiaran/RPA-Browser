"""
RabbitMQ RPC 客户端（兼容薄封装）。

实际实现已泛化下沉至 `bili_common.rpc.client.RpcClient`（amqp_url 由构造参数注入），
本文件仅保留 RPA-Browser 侧的单例与导入入口，保证存量调用点
（`from app.services.mq.rpc_client import rpc_client`）零改动：
- main.py 的 connect() / close()
- fetch_external_data.py 的 rpc_client.call(routing_key, payload, timeout)

RPC 流程（FastStream Blocking Request）：
1. connect() 调用 broker.start() 建立 RabbitMQ 连接并声明 reply 队列
2. call(routing_key, payload) 调用 broker.request() 发送消息并等待响应
3. FastStream 自动处理 correlation_id 和 reply_to
4. 解析响应消息体为 dict 返回
"""

from bili_common.rpc.client import RpcClient

from app.config import settings

__all__ = ["RpcClient", "rpc_client"]

# 全局单例
rpc_client = RpcClient(settings.rabbitmq_url)
