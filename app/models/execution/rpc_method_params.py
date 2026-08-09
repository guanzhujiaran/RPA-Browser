"""
RPC 方法请求参数模型

注意：RPC 参数模型（Get*RpcParams 等）已统一迁移至 bili_common.models.rpc_params，
本文件仅作为兼容入口 re-export，实际定义以 bili_common.models 为单一数据源。

字段定义与 FastapiApp 端（be-bilibili-crawler）保持一致，
通过 JSON 传递的 RPC 参数由 FastStream 自动 validate 为对应 SQLModel。
"""

from bili_common.models.rpc_params import (
    RPC_METHOD_PARAMS_MODEL_MAP,
    RPC_METHOD_PARAMS_FIELD_MAP,
)
from bili_common.models import (
    BaseLotteryRpcParams,
    GetReserveLotteryRpcParams,
    GetOfficialLotteryRpcParams,
    GetChargeLotteryRpcParams,
    GetTopicLotteryRpcParams,
    GetAllLotteryRpcParams,
    GetOthersLotDynListRpcParams,
)

__all__ = [
    "BaseLotteryRpcParams",
    "GetReserveLotteryRpcParams",
    "GetOfficialLotteryRpcParams",
    "GetChargeLotteryRpcParams",
    "GetTopicLotteryRpcParams",
    "GetAllLotteryRpcParams",
    "GetOthersLotDynListRpcParams",
    "RPC_METHOD_PARAMS_MODEL_MAP",
    "RPC_METHOD_PARAMS_FIELD_MAP",
]
