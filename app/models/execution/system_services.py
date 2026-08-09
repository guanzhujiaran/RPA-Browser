"""
系统 RPC 方法白名单定义

注意：RPC 方法契约（RpcMethodName / 路由键前缀 / 白名单 / 方法信息模型等）
已统一迁移至 bili_common.models，本文件仅作为兼容入口 re-export，
实际定义以 bili_common.models 为单一数据源。

routing_key 命名规范：`FastapiApp.rpc.<method_name>`
- 与 FastapiApp 侧 controller/v1/mq/rpc_server.py 保持一致
"""

from bili_common.models import (
    RpcMethodName,
    ROUTING_KEY_PREFIX,
    RpcMethodInfo,
    RpcMethodInfoResponse,
    ALLOWED_RPC_METHODS,
    routing_key_for,
    get_allowed_method_names,
    build_method_responses,
    validate_rpc_method,
)

__all__ = [
    "RpcMethodName",
    "ROUTING_KEY_PREFIX",
    "RpcMethodInfo",
    "RpcMethodInfoResponse",
    "ALLOWED_RPC_METHODS",
    "routing_key_for",
    "get_allowed_method_names",
    "build_method_responses",
    "validate_rpc_method",
]
