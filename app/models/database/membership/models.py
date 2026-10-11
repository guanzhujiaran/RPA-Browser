"""会员权益模块 - 时长账户 / 流水 / 月卡 / 签到 / 兑换码 / 使用统计

设计依据：docs/浏览器使用时长与会员权益计划书.md

约定：
    - mid 一律 BIGINT 存储；对外接口层负责 str <-> int 转换；
    - 时长统一用秒（int）表达，前端展示层格式化；
    - 仅定时任务工作流消耗时长，手动启动浏览器 / 手动运行不消耗。
"""

from datetime import date, datetime
from enum import Enum as PyEnum

from sqlalchemy import BIGINT, Enum as SAEnum, Index, UniqueConstraint
from sqlmodel import Field

from bili_common.models import StrEnumAutoDoc
from app.models.base.base_sqlmodel import BaseSQLModel


def enum_value_type(enum_cls: type[PyEnum]) -> SAEnum:
    """按枚举 value 存取的 SQLAlchemy ENUM 类型。

    SQLAlchemy 默认以枚举成员的 name（SIGN_IN）作为数据库取值，而本模块的
    alembic 迁移建列时用的是 value（sign_in），两者不一致会导致读取存量数据时
    抛 LookupError。此处显式指定 values_callable，使读写均以 value 为准。
    """
    return SAEnum(enum_cls, values_callable=lambda cls: [m.value for m in cls])


# region ============ 枚举 ============


class LedgerChangeType(StrEnumAutoDoc):
    """时长流水变动类型"""

    SIGN_IN = "sign_in"  # 签到奖励
    ACTIVITY = "activity"  # 活动赠送
    REDEEM = "redeem"  # 兑换码兑换（时长卡）
    PURCHASE = "purchase"  # 支付购买时长（Casdoor 商品入账）
    CONSUME = "consume"  # 定时工作流消耗
    ADJUST = "adjust"  # 后台人工调整
    MONTHLY_RESET = "monthly_reset"  # 月初赠送余额清零（剩余时长月底失效）


class MonthCardSourceEnum(StrEnumAutoDoc):
    """月卡来源"""

    REDEEM = "redeem"  # 兑换码
    ACTIVITY = "activity"  # 活动赠送
    ADMIN = "admin"  # 后台发放


class RedeemCodeTypeEnum(StrEnumAutoDoc):
    """兑换码类型"""

    DURATION = "duration"  # 时长卡：一次性时长包
    MONTH_CARD = "month_card"  # 月卡：N 天内定时工作流免扣


class SignRewardTierEnum(StrEnumAutoDoc):
    """签到累计奖励档位（标识与天数对应，具体门槛/奖励见 app/data/sign_in_rewards.json）"""

    D7 = "7d"  # 7 天档
    D14 = "14d"  # 14 天档
    D28 = "28d"  # 28 天档


# region ============ 表模型 ============


class UserDurationAccount(BaseSQLModel, table=True):
    """用户时长账户（一人一行，余额行锁扣减）

    余额双桶口径（不变式：balance_seconds = granted_balance_seconds +
    paid_balance_seconds，由 service 统一入口维护，禁止外部直改）：
    - granted_balance_seconds：赠送时长（签到 / 活动），每月初清零；
    - paid_balance_seconds：付费时长（时长卡兑换 / 支付购买 / 人工调整），永久保留；
    - 扣减时优先消耗赠送桶（见 MembershipService._charge_run_delta）。
    """

    __table_args__ = (UniqueConstraint("mid", name="uq_duration_account_mid"),)

    id: int | None = Field(default=None, primary_key=True)
    mid: int = Field(sa_type=BIGINT, index=True, description="用户ID")
    balance_seconds: int = Field(
        default=0, description="当前剩余时长（秒）= 赠送桶 + 付费桶"
    )
    granted_balance_seconds: int = Field(
        default=0, description="赠送时长余额（秒，签到/活动，每月初清零）"
    )
    paid_balance_seconds: int = Field(
        default=0, description="付费时长余额（秒，时长卡/购买/人工调整，永久保留）"
    )
    total_granted_seconds: int = Field(default=0, description="累计获得时长（秒）")
    total_consumed_seconds: int = Field(default=0, description="累计消耗时长（秒）")


class DurationLedger(BaseSQLModel, table=True):
    """时长变动流水（只追加，不修改）"""

    __table_args__ = (Index("idx_duration_ledger_mid_created", "mid", "created_at"),)

    id: int | None = Field(default=None, primary_key=True)
    mid: int = Field(sa_type=BIGINT, index=True, description="用户ID")
    change_seconds: int = Field(description="变动秒数（消耗为负，获得为正）")
    balance_after: int = Field(description="变动后余额（秒）")
    change_type: LedgerChangeType = Field(
        sa_type=enum_value_type(LedgerChangeType), index=True, description="变动类型"
    )
    ref_id: str = Field(
        default="", max_length=100, description="关联业务ID（兑换码/订单等）"
    )
    workflow_id: int | None = Field(
        default=None, sa_type=BIGINT, index=True, description="关联工作流ID（雪花 int）"
    )
    run_id: str | None = Field(
        default=None, max_length=64, description="关联工作流运行ID"
    )
    browser_id: int | None = Field(
        default=None, sa_type=BIGINT, description="关联浏览器ID"
    )
    remark: str = Field(default="", max_length=500, description="备注")


class MonthCardRecord(BaseSQLModel, table=True):
    """月卡记录（可叠加多张，任一有效即视为月卡生效中）"""

    __table_args__ = (Index("idx_month_card_mid_expire", "mid", "expire_at"),)

    id: int | None = Field(default=None, primary_key=True)
    mid: int = Field(sa_type=BIGINT, index=True, description="用户ID")
    source: MonthCardSourceEnum = Field(
        sa_type=enum_value_type(MonthCardSourceEnum), description="来源"
    )
    start_at: datetime = Field(description="生效时间")
    expire_at: datetime = Field(description="到期时间（不含）")
    ref_id: str = Field(
        default="", max_length=100, description="关联业务ID（兑换码等）"
    )
    remark: str = Field(default="", max_length=500, description="备注")

    def is_active(self, now: datetime) -> bool:
        return self.start_at <= now < self.expire_at


class SignInRecord(BaseSQLModel, table=True):
    """每日签到记录（含补签）

    - reward_seconds：当日实际发放秒数（基础固定时长 + 命中的里程碑额外奖励）；
    - continuous_days：历史字段（v1.3 的「连续签到天数」快照），新逻辑不再依赖，只读保留；
    - is_makeup / makeup_card_cost：补签标记与本次消耗的补登卡数（正常签到为 0）。
    """

    __table_args__ = (UniqueConstraint("mid", "sign_date", name="uq_signin_mid_date"),)

    id: int | None = Field(default=None, primary_key=True)
    mid: int = Field(sa_type=BIGINT, index=True, description="用户ID")
    sign_date: date = Field(description="签到日期（自然日）")
    reward_seconds: int = Field(description="本次奖励秒数")
    continuous_days: int = Field(
        default=1, description="历史字段：截至本次的连续签到天数（只读保留）"
    )
    is_makeup: bool = Field(default=False, description="是否补签产生")
    makeup_card_cost: int = Field(default=0, description="本次消耗的补登卡数量")


class UserSignProfile(BaseSQLModel, table=True):
    """用户签到档案（累计天数与补登卡持有量，一人一行）"""

    __table_args__ = (UniqueConstraint("mid", name="uq_user_sign_profile_mid"),)

    id: int | None = Field(default=None, primary_key=True)
    mid: int = Field(sa_type=BIGINT, index=True, description="用户ID")
    total_sign_days: int = Field(default=0, description="累计签到天数（含补签）")
    makeup_cards: int = Field(default=0, description="持有补登卡数量")


class SignRewardExchange(BaseSQLModel, table=True):
    """签到里程碑奖励档位兑换记录（每档每用户仅一次）"""

    __table_args__ = (
        UniqueConstraint("mid", "tier", name="uq_sign_reward_exchange_mid_tier"),
    )

    id: int | None = Field(default=None, primary_key=True)
    mid: int = Field(sa_type=BIGINT, index=True, description="用户ID")
    tier: SignRewardTierEnum = Field(
        sa_type=enum_value_type(SignRewardTierEnum), index=True, description="奖励档位"
    )
    reward_seconds: int = Field(default=0, description="发放时长秒数（快照）")
    makeup_cards: int = Field(default=0, description="发放补登卡数量（快照）")
    total_sign_days: int = Field(default=0, description="兑换时的累计签到天数（快照）")


class RedemptionCode(BaseSQLModel, table=True):
    """兑换码（时长卡 / 月卡）"""

    code: str = Field(
        primary_key=True, max_length=64, description="兑换码（明文，唯一）"
    )
    code_type: RedeemCodeTypeEnum = Field(
        sa_type=enum_value_type(RedeemCodeTypeEnum), description="码类型"
    )
    duration_seconds: int = Field(
        default=0, description="时长卡面值（秒）；MONTH_CARD 时为 0"
    )
    card_days: int = Field(default=0, description="月卡天数；DURATION 时为 0")
    max_uses: int = Field(default=1, description="最大兑换次数")
    used_count: int = Field(default=0, description="已兑换次数")
    is_enabled: bool = Field(default=True, description="是否启用")
    expire_at: datetime | None = Field(
        default=None, description="兑换码本身的有效期（None=永久）"
    )
    batch_no: str = Field(default="", max_length=64, index=True, description="批次号")
    remark: str = Field(default="", max_length=500, description="备注")


class CodeRedemptionRecord(BaseSQLModel, table=True):
    """兑换流水"""

    __table_args__ = (UniqueConstraint("code", "mid", name="uq_redeem_code_mid"),)

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(max_length=64, index=True, description="兑换码")
    mid: int = Field(sa_type=BIGINT, index=True, description="用户ID")
    reward_summary: str = Field(
        default="", max_length=200, description="兑换所得摘要（快照）"
    )


class BrowserUsageDailyStat(BaseSQLModel, table=True):
    """浏览器使用时长日统计（用户维度）

    - workflow_seconds：定时工作流运行时长（计费口径，手动运行也计入统计但另列）；
    - manual_seconds：手动调试 / 手动运行时长（不计费口径）。
    """

    __table_args__ = (
        UniqueConstraint(
            "mid", "stat_date", "browser_id", name="uq_usage_stat_mid_date_browser"
        ),
        # 日统计区间查询过滤条件为 mid + stat_date 范围（见 _MAX_USAGE_RANGE_DAYS）
        Index("idx_usage_stat_mid_date", "mid", "stat_date"),
    )

    id: int | None = Field(default=None, primary_key=True)
    mid: int = Field(sa_type=BIGINT, index=True, description="用户ID")
    stat_date: date = Field(index=True, description="统计日期")
    browser_id: int = Field(sa_type=BIGINT, description="浏览器ID")
    workflow_seconds: int = Field(
        default=0, description="工作流运行时长（秒，含定时/手动）"
    )
    manual_seconds: int = Field(default=0, description="手动调试会话时长（秒，不计费）")
    workflow_run_count: int = Field(default=0, description="工作流运行次数")


class DurationMonthlyReset(BaseSQLModel, table=True):
    """时长余额月度清零批次（每个自然月最多一条，幂等防重复执行）

    - period：被清零的月份（YYYY-MM，即该月最后一刻的余额在次月月初清零）；
    - reset_at：实际执行时间（次月月初 cron 触发时刻）；
    - affected_users / cleared_seconds：本次清零的账户数与清零总秒数（统计快照）。
    """

    __table_args__ = (UniqueConstraint("period", name="uq_duration_reset_period"),)

    id: int | None = Field(default=None, primary_key=True)
    period: str = Field(max_length=7, index=True, description="清零所属月份 YYYY-MM")
    reset_at: datetime = Field(description="实际执行时间")
    affected_users: int = Field(default=0, description="被清零的账户数")
    cleared_seconds: int = Field(default=0, description="清零总时长（秒）")
    remark: str = Field(default="", max_length=500, description="备注")


class PaymentGrantTypeEnum(StrEnumAutoDoc):
    """支付商品对应的权益类型（规格由商品名约定解析，见计划书 §3.3 方案A）"""

    DURATION = "duration"  # 入账时长
    MONTH_CARD = "month_card"  # 开通月卡（card_days 天）


class PaymentOrder(BaseSQLModel, table=True):
    """支付入账记录（幂等键 = Casdoor 支付单 name + 商品名）

    新版 Casdoor 购物车支付单可含多个商品（products 数组），故一笔支付单
    对应多行（每商品一行）；按 (payment_name, product_name) 先查后插防重放。
    """

    __table_args__ = (
        UniqueConstraint(
            "payment_name", "product_name", name="uq_payment_order_name_product"
        ),
        Index("idx_payment_order_mid_created", "mid", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    payment_name: str = Field(
        max_length=100, index=True, description="Casdoor 支付单 name"
    )
    casdoor_user: str = Field(
        default="", max_length=100, description="Casdoor 用户名（对账匹配用）"
    )
    mid: int = Field(sa_type=BIGINT, index=True, description="入账用户ID")
    product_name: str = Field(max_length=100, description="商品名")
    grant_type: PaymentGrantTypeEnum = Field(
        sa_type=enum_value_type(PaymentGrantTypeEnum),
        description="权益类型（入账时快照）",
    )
    duration_seconds: int = Field(default=0, description="入账时长（秒）")
    card_days: int = Field(default=0, description="月卡天数")
    price: float = Field(default=0.0, description="支付金额（快照）")


__all__ = [
    "LedgerChangeType",
    "MonthCardSourceEnum",
    "RedeemCodeTypeEnum",
    "PaymentGrantTypeEnum",
    "SignRewardTierEnum",
    "UserDurationAccount",
    "DurationLedger",
    "MonthCardRecord",
    "SignInRecord",
    "UserSignProfile",
    "SignRewardExchange",
    "RedemptionCode",
    "CodeRedemptionRecord",
    "BrowserUsageDailyStat",
    "DurationMonthlyReset",
    "PaymentOrder",
]
