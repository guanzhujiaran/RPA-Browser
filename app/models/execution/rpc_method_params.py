"""
RPC 方法请求参数 SQLModel 定义

为每个 RpcMethodName 提供独立的、强类型的请求参数模型，
替代旧的 Dict[str, Any] 通用字典结构，符合项目「复杂参数必须使用 SQLModel」规范。

字段定义与 FastapiApp 侧 handler 实际消费的参数一一对应：
- get_reserve_lottery / get_official_lottery / get_charge_lottery / get_topic_lottery
  → 继承 LotteryAdvancedQueryRpcParams（分页 + 高级筛选 + 排序）
- get_all_lottery → GetAllLotteryRpcParams（收录时间 preset + 时间范围）
- get_others_lot_dyn_list → GetOthersLotDynListRpcParams（分页 + 排序 + 时间筛选 + 是否抽奖）

枚举值与 FastapiApp 侧 Models/lottery_database/bili/LotteryDataModels.py 保持一致，
确保 RPC 传递的参数值能被 handler 正确解析。
"""
from enum import StrEnum

from pydantic import Field
from sqlmodel import SQLModel


# ============ 枚举定义（与 FastapiApp 侧保持一致） ============


class LotteryDataSortEnum(StrEnum):
    """抽奖数据排序字段枚举（用于预约/官方/充电/话题抽奖）"""
    lottery_time = "lottery_time"   # 开奖时间
    participants = "participants"   # 参与人数
    first_prize = "first_prize"     # 一等奖份数
    created_at = "created_at"       # 收录时间


class LotterySortOrderEnum(StrEnum):
    """通用排序方向枚举（抽奖数据）"""
    asc = "asc"
    desc = "desc"


class LotteryStatusEnum(StrEnum):
    """抽奖状态枚举（字符串值与 FastapiApp _parse_status 的 key 一致）"""
    unfinished = "unfinished"
    finished = "finished"
    canceled = "canceled"
    deleted = "deleted"
    unknown = "unknown"


class OthersLotDynSortEnum(StrEnum):
    """第三方抽奖动态排序字段枚举"""
    pub_time = "pubTime"
    created_at = "created_at"


class OthersLotDynSortOrderEnum(StrEnum):
    """第三方抽奖动态排序方向枚举"""
    asc = "asc"
    desc = "desc"


class TimePresetEnum(StrEnum):
    """时间快捷筛选枚举（值如 '1d'/'3d' 等，handler 会转为时间戳）"""
    last_1_day = "1d"
    last_3_days = "3d"
    last_5_days = "5d"
    last_7_days = "7d"
    last_14_days = "14d"
    last_30_days = "30d"


# ============ 通用基础参数 ============


class LotteryAdvancedQueryRpcParams(SQLModel):
    """抽奖高级查询 RPC 参数基类

    适用于 get_reserve_lottery / get_official_lottery / get_charge_lottery / get_topic_lottery，
    与 FastapiApp 侧 LotteryAdvancedQueryParams 字段一一对应。
    各子类按方法名独立定义，便于后续按方法差异扩展字段。

    注意：RPA 端作为 RPC client 不设置任何默认值，
    默认值由前端和 FastapiApp 端 RPC 服务端模型决定。
    """
    page_num: int | None = Field(default=None, ge=1, description="页码，从 1 开始，最小值为 1")
    page_size: int | None = Field(default=1000, ge=1, le=3000, description="每页数量，最大 3000，最小值为 1")

    # 时间范围筛选（开奖时间）
    start_ts: int | None = Field(default=None, ge=0, description="开奖时间起始（Unix 秒时间戳）")
    end_ts: int | None = Field(default=None, ge=0, description="开奖时间结束（Unix 秒时间戳）")

    # UP主筛选
    sender_uid: int | None = Field(default=None, ge=0, description="UP主 UID")

    # 参与人数筛选
    min_participants: int | None = Field(default=None, ge=0, description="最小参与人数")
    max_participants: int | None = Field(default=None, ge=0, description="最大参与人数")

    # 状态筛选
    status: LotteryStatusEnum | None = Field(
        default=None, description="抽奖状态: unfinished/finished/canceled/deleted/unknown")

    # 关键词
    keyword: str | None = Field(default=None, max_length=100, description="关键词搜索")

    # 排序
    sort_by: LotteryDataSortEnum | None = Field(
        default=None, description="排序字段: lottery_time/participants/first_prize/created_at")
    sort_order: LotterySortOrderEnum | None = Field(
        default=None, description="排序方向: asc/desc")

    # 时间快捷筛选（优先级高于 start_ts/end_ts 对应的精确时间字段）
    created_at_preset: TimePresetEnum | None = Field(
        default=TimePresetEnum.last_7_days, description="收录时间快捷筛选: 1d/3d/5d/7d/14d/30d")
    pub_time_preset: TimePresetEnum | None = Field(
        default=TimePresetEnum.last_7_days, description="发布时间快捷筛选: 1d/3d/5d/7d/14d/30d")


# ============ 各方法独立参数模型 ============


class GetReserveLotteryRpcParams(LotteryAdvancedQueryRpcParams):
    """get_reserve_lottery 方法请求参数 - 获取预约抽奖数据"""
    pass


class GetOfficialLotteryRpcParams(LotteryAdvancedQueryRpcParams):
    """get_official_lottery 方法请求参数 - 获取官方抽奖数据"""
    pass


class GetChargeLotteryRpcParams(LotteryAdvancedQueryRpcParams):
    """get_charge_lottery 方法请求参数 - 获取充电抽奖数据"""
    pass


class GetTopicLotteryRpcParams(LotteryAdvancedQueryRpcParams):
    """get_topic_lottery 方法请求参数 - 获取话题抽奖数据

    handler 当前仅消费 page_num/page_size/keyword，但接受完整高级查询参数，
    保留全部字段以便后续扩展无需修改模型。
    """
    pass


class GetAllLotteryRpcParams(SQLModel):
    """get_all_lottery 方法请求参数 - 按收录时间和发布时间获取全部抽奖信息

    注意：RPA 端作为 RPC client 不设置任何默认值，
    默认值由前端和 FastapiApp 端 RPC 服务端模型决定。
    """
    page_num: int | None = Field(default=None, ge=1, description="页码，从 1 开始，最小值为 1")
    page_size: int | None = Field(
        default=None, ge=1, le=1000, description="每页数量，最大 1000，最小值为 1"
    )
    created_at_preset: TimePresetEnum | None = Field(
        default=None,
        description="收录时间快捷筛选: 1d/3d/5d/7d/14d/30d",
    )
    created_at_start: int | None = Field(
        default=None, ge=0, description="收录起始时间（Unix 秒），preset 优先级高于此字段"
    )
    created_at_end: int | None = Field(
        default=None, ge=0, description="收录结束时间（Unix 秒）"
    )
    pub_time_preset: TimePresetEnum | None = Field(
        default=None,
        description="发布时间快捷筛选: 1d/3d/5d/7d/14d/30d",
    )
    pub_time_start: int | None = Field(
        default=None, ge=0, description="发布起始时间（Unix 秒），preset 优先级高于此字段"
    )
    pub_time_end: int | None = Field(
        default=None, ge=0, description="发布结束时间（Unix 秒）"
    )


class GetOthersLotDynListRpcParams(SQLModel):
    """get_others_lot_dyn_list 方法请求参数 - 获取第三方抽奖动态列表

    与 FastapiApp 侧 handle_get_others_lot_dyn_list 消费的参数一一对应。

    注意：RPA 端作为 RPC client 不设置任何默认值，
    默认值由前端和 FastapiApp 端 RPC 服务端模型决定。
    """
    page_num: int | None = Field(default=None, ge=1, description="页码，从 1 开始，最小值为 1")
    page_size: int | None = Field(default=None, ge=1, le=3000, description="每页数量，最大 3000，最小值为 1")

    # 排序
    sort_by: OthersLotDynSortEnum | None = Field(
        default=None,
        description="排序字段: pubTime(发布时间)/created_at(收录时间)")
    sort_order: OthersLotDynSortOrderEnum | None = Field(
        default=None,
        description="排序方向: asc/desc")

    # 是否抽奖
    is_lot: bool | None = Field(default=None, description="是否筛选为抽奖的动态")

    # 时间快捷筛选（handler 内部会转换为对应时间戳，优先级高于精确时间字段）
    created_at_preset: TimePresetEnum | None = Field(
        default=None, description="收录时间快捷筛选: 1d/3d/5d/7d/14d/30d")
    pub_time_preset: TimePresetEnum | None = Field(
        default=None, description="发布时间快捷筛选: 1d/3d/5d/7d/14d/30d")

    # 精确时间范围筛选（Unix 秒时间戳）
    pub_time_start: int | None = Field(default=None, ge=0, description="发布起始时间（Unix 秒）")
    pub_time_end: int | None = Field(default=None, ge=0, description="发布结束时间（Unix 秒）")
    created_at_start: int | None = Field(default=None, ge=0, description="收录起始时间（Unix 秒）")
    created_at_end: int | None = Field(default=None, ge=0, description="收录结束时间（Unix 秒）")


# ============ 方法名 → 参数模型/字段名映射 ============

# FetchExternalDataParams 中各 RPC 方法对应的强类型参数模型类
# 用于根据 method_name 获取参数模型类型（前端 Schema 自动生成、后续校验等）
RPC_METHOD_PARAMS_MODEL_MAP: dict[str, type[SQLModel]] = {
    "get_reserve_lottery": GetReserveLotteryRpcParams,
    "get_official_lottery": GetOfficialLotteryRpcParams,
    "get_charge_lottery": GetChargeLotteryRpcParams,
    "get_topic_lottery": GetTopicLotteryRpcParams,
    "get_all_lottery": GetAllLotteryRpcParams,
    "get_others_lot_dyn_list": GetOthersLotDynListRpcParams,
}

# method_name → FetchExternalDataParams 中的字段名
# 用于 _execute_via_rpc 根据 method_name 读取对应的强类型参数实例
RPC_METHOD_PARAMS_FIELD_MAP: dict[str, str] = {
    "get_reserve_lottery": "get_reserve_lottery_params",
    "get_official_lottery": "get_official_lottery_params",
    "get_charge_lottery": "get_charge_lottery_params",
    "get_topic_lottery": "get_topic_lottery_params",
    "get_all_lottery": "get_all_lottery_params",
    "get_others_lot_dyn_list": "get_others_lot_dyn_list_params",
}
