"""时长与会员权益路由模块

提供浏览器使用时长体系的用户侧 API（全部 POST，避免缓存等问题）：
- 账户概览（余额 / 月卡状态 / 今日签到状态）
- 每日签到
- 兑换码兑换（时长卡 / 月卡）
- 时长流水分页查询
- 使用时长日统计查询

约定：数值字段对前端一律 str（前端 JS 无法安全表达超过 Number.MAX_SAFE_INTEGER 的整数），
后端接收 str 自行转 int（见 app/models/database/membership/models.py 与计划书）。
"""

import re
from calendar import monthrange
from collections.abc import Sequence
from datetime import date

from fastapi import Depends
from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from bili_common.models.response import (
    StandardResponse,
    success_response,
)
from app.models.base.base_sqlmodel import BasePaginationReq, BasePaginationResp
from app.models.common.exceptions.base_exception import (
    DateRangeTooLargeException,
    InvalidLedgerChangeTypeException,
    InvalidMembershipParamException,
    InvalidSignInDateException,
    InvalidSignInMonthException,
    InvalidSignRewardTierException,
)
from app.models.database.membership.models import (
    BrowserUsageDailyStat,
    DurationLedger,
    LedgerChangeType,
    MonthCardRecord,
    SignInRecord,
    SignRewardTierEnum,
    UserDurationAccount,
)
from app.services.membership.membership_service import (
    MembershipService,
    build_redeem_summary,
)
from app.services.membership.sign_reward_config_service import (
    SignRewardConfig,
    SignRewardConfigService,
)
from app.utils.controller.router_path import gen_api_router
from app.utils.time_util import today_local
from app.models.router.all_routes import duration_membership_router
from app.models.router.router_prefix import BrowserMembershipRouterPath
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.session_manager import DatabaseSessionManager

router = gen_api_router(duration_membership_router)


# ============ 请求 / 响应模型 ============


class EmptyRequest(SQLModel):
    """空请求体占位（所有 POST 语义但无入参的接口统一复用）"""

    pass


class RedeemRequest(SQLModel):
    """兑换码兑换请求"""

    code: str = Field(min_length=1, max_length=64, description="兑换码")


class LedgerListRequest(BasePaginationReq):
    """时长流水分页请求

    change_types 为逗号分隔的变动类型（见 LedgerChangeType）：
    留空 = 不过滤（全部）；`consume` = 仅消耗；
    `sign_in,activity,redeem,adjust` = 仅获得。非法取值由 pydantic 校验拦截。
    """

    change_types: str = Field(
        default="", max_length=100, description="变动类型过滤，逗号分隔；空=全部"
    )


def parse_ledger_change_types(raw: str) -> list[LedgerChangeType]:
    """把逗号分隔的过滤串解析为枚举列表（空串 = 不过滤，返回空列表）"""
    values = [item.strip() for item in raw.split(",") if item.strip()]
    parsed: list[LedgerChangeType] = []
    for value in values:
        try:
            parsed.append(LedgerChangeType(value))
        except ValueError as exc:
            raise InvalidLedgerChangeTypeException(value) from exc
    return parsed


class UsageStatsRequest(SQLModel):
    """使用日统计查询请求"""

    start_date: str = Field(description="开始日期 YYYY-MM-DD")
    end_date: str = Field(description="结束日期 YYYY-MM-DD")
    browser_id: str | None = Field(
        default=None, description="浏览器ID（str，可选过滤）"
    )


class MonthCardInfo(SQLModel):
    """月卡状态信息"""

    active: bool = Field(description="是否有生效中的月卡")
    start_at: str | None = Field(default=None, description="生效时间")
    expire_at: str | None = Field(default=None, description="到期时间")

    @classmethod
    def from_record(cls, card: MonthCardRecord | None) -> "MonthCardInfo":
        if card is None:
            return cls(active=False)
        return cls(
            active=True,
            start_at=card.start_at.isoformat(sep=" ", timespec="seconds"),
            expire_at=card.expire_at.isoformat(sep=" ", timespec="seconds"),
        )


class DurationAccountResponse(SQLModel):
    """账户概览响应（数值以 str 传递）"""

    balance_seconds: str = Field(description="剩余时长（秒）")
    total_granted_seconds: str = Field(description="累计获得（秒）")
    total_consumed_seconds: str = Field(description="累计消耗（秒）")
    month_card: MonthCardInfo = Field(description="月卡状态")
    signed_today: bool = Field(description="今日是否已签到")

    @classmethod
    def from_account(
        cls,
        account: UserDurationAccount,
        month_card: MonthCardInfo,
        signed_today: bool,
    ) -> "DurationAccountResponse":
        return cls(
            balance_seconds=str(account.balance_seconds),
            total_granted_seconds=str(account.total_granted_seconds),
            total_consumed_seconds=str(account.total_consumed_seconds),
            month_card=month_card,
            signed_today=signed_today,
        )


class SignInResponse(SQLModel):
    """签到结果响应"""

    reward_seconds: str = Field(
        description="本次奖励秒数（固定时长 + 命中的里程碑额外奖励）"
    )
    continuous_days: str = Field(
        description="累计签到天数（历史字段名保留，语义为累计）"
    )
    balance_seconds: str = Field(description="签到后余额（秒）")
    total_sign_days: str = Field(default="0", description="本次签到后的累计签到天数")
    makeup_cards: str = Field(default="0", description="当前持有补登卡数量")
    next_tier: str = Field(default="", description="下一个未达成档位；全部达成为空串")


class RedeemResponse(SQLModel):
    """兑换结果响应"""

    code_type: str = Field(description="码类型: duration / month_card")
    duration_seconds: str = Field(default="0", description="获得的时长（秒）")
    card_days: str = Field(default="0", description="月卡天数")
    reward_summary: str = Field(description="兑换所得摘要")
    balance_seconds: str = Field(description="兑换后余额（秒）")


class DurationLedgerItem(SQLModel):
    """时长流水条目"""

    id: str = Field(description="流水ID")
    change_seconds: str = Field(description="变动秒数（负=消耗）")
    balance_after: str = Field(description="变动后余额（秒）")
    change_type: str = Field(description="变动类型")
    ref_id: str = Field(description="关联业务ID")
    workflow_id: str = Field(description="关联工作流ID")
    run_id: str = Field(description="关联运行ID")
    browser_id: str = Field(description="关联浏览器ID")
    remark: str = Field(description="备注")
    created_at: str = Field(description="时间")

    @classmethod
    def from_record(cls, record: DurationLedger) -> "DurationLedgerItem":
        return cls(
            id=str(record.id or 0),
            change_seconds=str(record.change_seconds),
            balance_after=str(record.balance_after),
            change_type=record.change_type.value
            if isinstance(record.change_type, LedgerChangeType)
            else str(record.change_type),
            ref_id=record.ref_id or "",
            workflow_id=record.workflow_id or "",
            run_id=record.run_id or "",
            browser_id=str(record.browser_id) if record.browser_id else "",
            remark=record.remark or "",
            created_at=record.created_at.isoformat(sep=" ", timespec="seconds"),
        )


class LedgerListResponse(BasePaginationResp[DurationLedgerItem]):
    """时长流水分页响应"""

    pass


class UsageStatItem(SQLModel):
    """使用日统计条目"""

    stat_date: str = Field(description="统计日期")
    browser_id: str = Field(description="浏览器ID")
    workflow_seconds: str = Field(description="工作流运行时长（秒）")
    manual_seconds: str = Field(description="手动调试时长（秒，不计费）")
    workflow_run_count: str = Field(description="工作流运行次数")

    @classmethod
    def from_record(cls, record: BrowserUsageDailyStat) -> "UsageStatItem":
        return cls(
            stat_date=record.stat_date.isoformat(),
            browser_id=str(record.browser_id),
            workflow_seconds=str(record.workflow_seconds),
            manual_seconds=str(record.manual_seconds),
            workflow_run_count=str(record.workflow_run_count),
        )


class UsageStatsResponse(SQLModel):
    """使用日统计响应"""

    items: list[UsageStatItem] = Field(default_factory=list)


class SignInCalendarRequest(SQLModel):
    """签到日历查询请求"""

    month: str | None = Field(
        default=None, description="查询月份 YYYY-MM（可选，缺省为当前月）"
    )


# ============ 响应模型 ============


class SignInCalendarDay(SQLModel):
    """签到日历单日条目"""

    date: str = Field(description="日期 YYYY-MM-DD")
    signed: bool = Field(description="当天是否已签到")
    reward_seconds: str = Field(description="当天签到奖励（秒）；未签到为 0")
    continuous_days: str = Field(
        description="历史字段：截至当天的连续签到天数（只读保留）"
    )
    is_makeup: bool = Field(default=False, description="当天是否为补签")
    can_makeup: bool = Field(
        default=False, description="当天是否可补登（当月 / 早于今天 / 未签到）"
    )


class SignMilestoneInfo(SQLModel):
    """下一个未达成档位信息"""

    tier: str = Field(description="档位标识: 7d / 14d / 28d")
    days: str = Field(description="该档位所需累计签到天数")
    remaining: str = Field(description="还差的天数")
    reward_seconds: str = Field(description="该档位发放时长（秒）")
    makeup_cards: str = Field(description="该档位发放补登卡数量")


class SignInCalendarResponse(SQLModel):
    """签到日历响应（当月逐日补齐，数值以 str 传递）"""

    month: str = Field(description="查询月份 YYYY-MM")
    days: list[SignInCalendarDay] = Field(
        default_factory=list, description="当月 1 号至月末逐日条目"
    )
    signed_days: str = Field(description="当月已签到天数")
    total_reward_seconds: str = Field(description="当月签到合计奖励（秒）")
    current_continuous_days: str = Field(
        description="历史字段：最新连续签到天数（无记录为 0）"
    )
    total_sign_days: str = Field(default="0", description="累计签到天数（含补签）")
    makeup_cards: str = Field(default="0", description="持有补登卡数量")
    next_milestone: SignMilestoneInfo | None = Field(
        default=None, description="下一个未达成的档位；全部达成时为 null"
    )


class SignMakeupRequest(SQLModel):
    """补签请求"""

    date: str = Field(description="补签日期 YYYY-MM-DD（仅未签到的过去日期）")


class SignMakeupResponse(SQLModel):
    """补签结果响应"""

    date: str = Field(description="补签日期")
    reward_seconds: str = Field(description="本次发放时长（秒）")
    total_sign_days: str = Field(description="补签后的累计签到天数")
    makeup_cards: str = Field(description="补签后剩余补登卡数量")
    balance_seconds: str = Field(description="补签后时长余额（秒）")


class SignOverviewStreak(SQLModel):
    """签到概览 - 累计与可补登信息"""

    days: str = Field(description="累计签到天数（断签不清零）")
    month_total_days: str = Field(description="当月已签到天数（含补签）")
    month_consumed_days: str = Field(description="当月补签（消耗补登卡）天数")
    next_tier: str = Field(default="", description="下一个未达成档位；全部达成为空串")
    next_tier_remaining: str = Field(default="0", description="距下一档位还差天数")
    makeup_dates: list[str] = Field(
        default_factory=list, description="当前可补登的日期 YYYY-MM-DD"
    )


class SignOverviewMakeupCards(SQLModel):
    """签到概览 - 补登卡"""

    balance: str = Field(description="持有补登卡数量")
    max: str = Field(description="补登卡持有上限（配置）")


class SignOverviewTierItem(SQLModel):
    """签到概览 - 档位状态条目"""

    tier: str = Field(description="档位标识: 7d / 14d / 28d")
    name: str = Field(description="档位名称")
    days: str = Field(description="解锁所需累计签到天数")
    reward_seconds: str = Field(description="发放时长（秒）")
    makeup_cards: str = Field(description="发放补登卡数量")
    status: str = Field(
        description="locked=未解锁 / unlocked=可兑换 / exchanged=已兑换"
    )
    exchanged: bool = Field(description="是否已兑换")


class SignOverviewRedemptionStatus(SQLModel):
    """签到概览 - 档位兑换状态"""

    total_sign_days: str = Field(description="当前累计签到天数")
    remaining_days: str = Field(default="0", description="距下一档位还差天数")
    tiers: list[SignOverviewTierItem] = Field(
        default_factory=list, description="各档位状态"
    )


class SignOverviewResponse(SQLModel):
    """签到概览响应（结构对齐外部抓包，资产项按本项目：时长 + 补登卡）"""

    streak: SignOverviewStreak = Field(description="累计签到与可补登信息")
    makeup_cards: SignOverviewMakeupCards = Field(description="补登卡持有情况")
    redemption_status: SignOverviewRedemptionStatus = Field(description="档位兑换状态")
    launch_date: str = Field(description="玩法上线日期（配置）")
    timezone: str = Field(description="签到判定时区（配置）")


class SignRewardExchangeRequest(SQLModel):
    """奖励档位兑换请求"""

    tier: str = Field(description="档位标识: 7d / 14d / 28d")


class SignRewardExchangeResponse(SQLModel):
    """奖励档位兑换结果响应"""

    tier: str = Field(description="档位标识")
    name: str = Field(description="档位名称")
    reward_seconds: str = Field(description="发放时长（秒）")
    makeup_cards: str = Field(description="实际发放补登卡数量（受上限封顶）")
    total_sign_days: str = Field(description="兑换时的累计签到天数")
    balance_seconds: str = Field(description="兑换后时长余额（秒）")


def parse_sign_in_calendar_month(raw: str | None, today: date) -> date:
    """把 YYYY-MM 解析为该月任意一天；空值取当前月"""
    if not raw:
        return today.replace(day=1)
    try:
        year, month = (int(part) for part in raw.split("-", maxsplit=1))
        return date(year, month, 1)
    except (TypeError, ValueError) as exc:
        raise InvalidSignInMonthException(raw) from exc


def parse_sign_date(raw: str) -> date:
    """把 YYYY-MM-DD 解析为日期"""
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidSignInDateException(raw) from exc


def parse_optional_browser_id(raw: str | None) -> int | None:
    """把 str 形态的浏览器 ID 转 int（非法值抛业务异常而不是 500）"""
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidMembershipParamException(f"浏览器ID 非法: {raw}") from exc
    if value <= 0:
        raise InvalidMembershipParamException(f"浏览器ID 非法: {raw}")
    return value


# Casdoor payment 的 owner / name 允许字符（会被拼进管理端 URL 路径）
_CASDOOR_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def parse_casdoor_identifier(raw: str, field: str) -> str:
    """校验 Casdoor owner / name 字符白名单（防 URL 路径注入）

    结合 casdoor_payment_client 侧的 quote 双重防护：白名单拒绝非常规字符，
    quote 保证即便漏网也不会改变 URL 路径结构。
    """
    if not _CASDOOR_IDENTIFIER.match(raw or ""):
        raise InvalidMembershipParamException(f"{field} 非法: {raw or '<empty>'}")
    return raw


def parse_usage_date_range(start_raw: str, end_raw: str) -> tuple[date, date]:
    """解析并校验统计查询区间（顺序合理、跨度有限）"""
    start = parse_sign_date(start_raw)
    end = parse_sign_date(end_raw)
    if end < start:
        raise InvalidMembershipParamException(
            f"结束日期不能早于开始日期: {start_raw} ~ {end_raw}"
        )
    days = (end - start).days + 1
    if days > MembershipService._MAX_USAGE_RANGE_DAYS:
        raise DateRangeTooLargeException(days, MembershipService._MAX_USAGE_RANGE_DAYS)
    return start, end


def build_sign_in_calendar(
    month: date,
    records: Sequence[SignInRecord],
    *,
    total_sign_days: int,
    makeup_cards: int,
    makeupable_dates: Sequence[date],
    config: SignRewardConfig,
    today: date | None = None,
) -> SignInCalendarResponse:
    """把当月签到记录补齐为整月日历，并汇总天数、奖励与下一档位进度

    can_makeup 口径由服务层给出（`makeupable_dates`：未签到且早于今天的补签窗口内日期）。
    """
    today = today or today_local()
    by_date: dict[date, SignInRecord] = {r.sign_date: r for r in records}
    makeupable = set(makeupable_dates)
    total_days = monthrange(month.year, month.month)[1]
    days: list[SignInCalendarDay] = []
    total_reward = 0
    for day_number in range(1, total_days + 1):
        current = month.replace(day=day_number)
        record = by_date.get(current)
        if record is None:
            days.append(
                SignInCalendarDay(
                    date=current.isoformat(),
                    signed=False,
                    reward_seconds="0",
                    continuous_days="0",
                    is_makeup=False,
                    can_makeup=current in makeupable,
                )
            )
            continue
        total_reward += record.reward_seconds
        days.append(
            SignInCalendarDay(
                date=current.isoformat(),
                signed=True,
                reward_seconds=str(record.reward_seconds),
                continuous_days=str(record.continuous_days),
                is_makeup=bool(record.is_makeup),
                can_makeup=False,
            )
        )
    latest = records[-1] if records else None
    next_tier = config.next_tier(total_sign_days)
    return SignInCalendarResponse(
        month=month.strftime("%Y-%m"),
        days=days,
        signed_days=str(len(records)),
        total_reward_seconds=str(total_reward),
        current_continuous_days=str(latest.continuous_days if latest else 0),
        total_sign_days=str(total_sign_days),
        makeup_cards=str(makeup_cards),
        next_milestone=(
            SignMilestoneInfo(
                tier=next_tier.tier,
                days=str(next_tier.days),
                remaining=str(next_tier.days - total_sign_days),
                reward_seconds=str(next_tier.reward_seconds),
                makeup_cards=str(next_tier.makeup_cards),
            )
            if next_tier
            else None
        ),
    )


# ============ API ============


@router.post(BrowserMembershipRouterPath.get_account)
async def get_membership_account(
    req: EmptyRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[DurationAccountResponse]:
    """获取当前用户的时长账户概览（余额 / 月卡状态 / 今日签到状态）"""
    account = await MembershipService.get_or_create_account(auth.mid, session)
    card = await MembershipService.get_active_month_card(auth.mid, session)
    signed_today = await MembershipService.is_signed_today(auth.mid, session)
    # commit 前先构建响应（expire_on_commit=True，commit 后 ORM 属性过期不可读）
    response = DurationAccountResponse.from_account(
        account, MonthCardInfo.from_record(card), signed_today
    )
    await session.commit()  # 懒创建的账户行落库（避免只 flush 被回滚）
    return success_response(response)


@router.post(BrowserMembershipRouterPath.sign_in)
async def sign_in(
    req: EmptyRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[SignInResponse]:
    """每日签到（重复签到返回业务码 5005）

    奖励口径：每天发配置里的固定时长（`sign_in_rewards.json` 的 `daily_reward_seconds`）；
    累计天数只用于档位解锁，签到当天不再自动叠加。
    """
    record = await MembershipService.sign_in(auth.mid, session)
    account = await MembershipService.get_or_create_account(auth.mid, session)
    profile = await MembershipService.get_or_create_sign_profile(auth.mid, session)
    config = await SignRewardConfigService.get_config()
    next_tier = config.next_tier(profile.total_sign_days)
    # commit 前先取标量（expire_on_commit=True，commit 后 ORM 属性过期不可读）
    response = SignInResponse(
        reward_seconds=str(record.reward_seconds),
        continuous_days=str(record.continuous_days),
        balance_seconds=str(account.balance_seconds),
        total_sign_days=str(profile.total_sign_days),
        makeup_cards=str(profile.makeup_cards),
        next_tier=next_tier.tier if next_tier else "",
    )
    await session.commit()
    return success_response(response)


@router.post(BrowserMembershipRouterPath.sign_in_overview)
async def get_sign_in_overview(
    req: EmptyRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[SignOverviewResponse]:
    """签到概览：累计天数 / 补登卡 / 档位状态 / 上线与时区（配置）"""
    today = today_local()
    month = today.replace(day=1)
    config = await SignRewardConfigService.get_config()
    profile = await MembershipService.get_or_create_sign_profile(auth.mid, session)
    records = await MembershipService.list_sign_in_records(auth.mid, session, month)
    makeupable = await MembershipService.list_makeupable_dates(auth.mid, month, session)
    exchanges = await MembershipService.list_sign_reward_exchanges(auth.mid, session)
    exchanged = {
        item.tier.value if isinstance(item.tier, SignRewardTierEnum) else str(item.tier)
        for item in exchanges
    }
    total_days = profile.total_sign_days
    makeup_cards = profile.makeup_cards
    next_tier = config.next_tier(total_days)
    # commit 前先取标量（expire_on_commit=True，commit 后 ORM 属性过期不可读）
    month_total_days = len(records)
    month_consumed_days = sum(1 for r in records if r.is_makeup)
    makeup_dates = [d.isoformat() for d in makeupable]
    await session.commit()  # 懒创建的签到档案行落库
    return success_response(
        SignOverviewResponse(
            streak=SignOverviewStreak(
                days=str(total_days),
                month_total_days=str(month_total_days),
                month_consumed_days=str(month_consumed_days),
                next_tier=next_tier.tier if next_tier else "",
                next_tier_remaining=(
                    str(next_tier.days - total_days) if next_tier else "0"
                ),
                makeup_dates=makeup_dates,
            ),
            makeup_cards=SignOverviewMakeupCards(
                balance=str(makeup_cards),
                max=str(config.makeup_card_max),
            ),
            redemption_status=SignOverviewRedemptionStatus(
                total_sign_days=str(total_days),
                remaining_days=(str(next_tier.days - total_days) if next_tier else "0"),
                tiers=[
                    SignOverviewTierItem(
                        tier=item.tier,
                        name=f"{item.tier} 档",
                        days=str(item.days),
                        reward_seconds=str(item.reward_seconds),
                        makeup_cards=str(item.makeup_cards),
                        status=(
                            "exchanged"
                            if item.tier in exchanged
                            else ("unlocked" if total_days >= item.days else "locked")
                        ),
                        exchanged=item.tier in exchanged,
                    )
                    for item in config.tiers
                ],
            ),
            launch_date=config.launch_date,
            timezone=config.timezone,
        )
    )


@router.post(BrowserMembershipRouterPath.sign_in_calendar)
async def get_sign_in_calendar(
    req: SignInCalendarRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[SignInCalendarResponse]:
    """查询指定月份的签到日历（整月逐日补齐 + 天数/奖励/下一档位）"""
    today = today_local()
    month = parse_sign_in_calendar_month(req.month, today)
    config = await SignRewardConfigService.get_config()
    records = await MembershipService.list_sign_in_records(auth.mid, session, month)
    profile = await MembershipService.get_or_create_sign_profile(auth.mid, session)
    makeupable = await MembershipService.list_makeupable_dates(auth.mid, month, session)
    # commit 前先构建响应（expire_on_commit=True，commit 后 ORM 属性过期不可读）
    response = build_sign_in_calendar(
        month,
        records,
        total_sign_days=profile.total_sign_days,
        makeup_cards=profile.makeup_cards,
        makeupable_dates=makeupable,
        config=config,
        today=today,
    )
    await session.commit()  # 懒创建的签到档案行落库
    return success_response(response)


@router.post(BrowserMembershipRouterPath.sign_in_makeup)
async def makeup_sign_in(
    req: SignMakeupRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[SignMakeupResponse]:
    """补签指定日期（未签到的过去日期，消耗 1 张补登卡）"""
    target = parse_sign_date(req.date)
    record = await MembershipService.makeup_sign_in(auth.mid, target, session)
    account = await MembershipService.get_or_create_account(auth.mid, session)
    profile = await MembershipService.get_or_create_sign_profile(auth.mid, session)
    # commit 前先取标量（expire_on_commit=True，commit 后 ORM 属性过期不可读）
    response = SignMakeupResponse(
        date=target.isoformat(),
        reward_seconds=str(record.reward_seconds),
        total_sign_days=str(profile.total_sign_days),
        makeup_cards=str(profile.makeup_cards),
        balance_seconds=str(account.balance_seconds),
    )
    await session.commit()
    return success_response(response)


@router.post(BrowserMembershipRouterPath.sign_in_reward_exchange)
async def exchange_sign_reward(
    req: SignRewardExchangeRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[SignRewardExchangeResponse]:
    """兑换奖励档位（每档每用户仅一次；奖励与门槛取自 JSON 配置）"""
    try:
        tier = SignRewardTierEnum(req.tier)
    except ValueError as exc:
        raise InvalidSignRewardTierException(req.tier) from exc
    record = await MembershipService.exchange_sign_reward(auth.mid, tier, session)
    account = await MembershipService.get_or_create_account(auth.mid, session)
    profile = await MembershipService.get_or_create_sign_profile(auth.mid, session)
    # commit 前先取标量（expire_on_commit=True，commit 后 ORM 属性过期不可读）
    response = SignRewardExchangeResponse(
        tier=record.tier.value
        if isinstance(record.tier, SignRewardTierEnum)
        else str(record.tier),
        name=f"{tier.value} 档",
        reward_seconds=str(record.reward_seconds),
        makeup_cards=str(record.makeup_cards),
        total_sign_days=str(profile.total_sign_days),
        balance_seconds=str(account.balance_seconds),
    )
    await session.commit()
    return success_response(response)


@router.post(BrowserMembershipRouterPath.redeem)
async def redeem_code(
    req: RedeemRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[RedeemResponse]:
    """兑换码兑换（时长卡直加余额 / 月卡按天顺延）"""
    code = await MembershipService.redeem_code(auth.mid, req.code, session)
    account = await MembershipService.get_or_create_account(auth.mid, session)
    # commit 前先取标量（expire_on_commit=True，commit 后 ORM 属性过期不可读）
    # 枚举取值用 getattr 兜底：无论 SQLAlchemy 返回的是枚举成员还是原始 str 都可用
    code_type_val = str(getattr(code.code_type, "value", code.code_type))
    response = RedeemResponse(
        code_type=code_type_val,
        duration_seconds=str(code.duration_seconds),
        card_days=str(code.card_days),
        reward_summary=build_redeem_summary(code),
        balance_seconds=str(account.balance_seconds),
    )
    await session.commit()
    return success_response(response)


@router.post(BrowserMembershipRouterPath.ledger_list)
async def list_duration_ledger(
    req: LedgerListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[LedgerListResponse]:
    """分页查询时长流水（时间倒序；change_types 支持按变动类型过滤）"""
    result = await MembershipService.list_ledger(
        auth.mid,
        session,
        req,
        change_types=parse_ledger_change_types(req.change_types),
    )
    return success_response(
        LedgerListResponse(
            page=result.page,
            per_page=result.per_page,
            total=result.total,
            items=[DurationLedgerItem.from_record(i) for i in result.items],
        )
    )


@router.post(BrowserMembershipRouterPath.usage_stats)
async def list_usage_stats(
    req: UsageStatsRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[UsageStatsResponse]:
    """按日期区间查询使用时长日统计（手动调试不计费口径单列）"""
    start, end = parse_usage_date_range(req.start_date, req.end_date)
    browser_id = parse_optional_browser_id(req.browser_id)
    items = await MembershipService.list_usage_stats(
        auth.mid,
        session,
        start_date=start,
        end_date=end,
        browser_id=browser_id,
    )
    return success_response(
        UsageStatsResponse(items=[UsageStatItem.from_record(i) for i in items])
    )


# ============ 支付（Casdoor 收银台，入账只认服务端对账，见计划书 §3.3） ============


class PaymentProductItem(SQLModel):
    """支付商品条目"""

    product_name: str = Field(description="Casdoor 商品名（购买页 URL 用）")
    display_name: str = Field(description="展示名")
    price: str = Field(description="价格（元，仅展示）")
    grant_type: str = Field(description="权益类型: duration / month_card")
    duration_seconds: str = Field(description="入账时长（秒）")
    card_days: str = Field(description="月卡天数")
    buy_url: str = Field(description="Casdoor 收银台购买 URL")


class PaymentProductsResponse(SQLModel):
    """支付商品列表响应"""

    items: list[PaymentProductItem] = Field(default_factory=list)


class PaymentGrantedItem(SQLModel):
    """单笔入账结果"""

    product: str = Field(description="商品展示名")
    summary: str = Field(description="入账摘要")


class PaymentConfirmResponse(SQLModel):
    """支付对账结果响应"""

    granted: list[PaymentGrantedItem] = Field(default_factory=list)


@router.post(BrowserMembershipRouterPath.payment_products)
async def list_payment_products(
    req: EmptyRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[PaymentProductsResponse]:
    """可售支付商品列表（实时拉取 Casdoor 并按命名约定解析规格；buy_url 空=未启用）"""
    products = await MembershipService.list_store_products()
    return success_response(
        PaymentProductsResponse(
            items=[
                PaymentProductItem(
                    product_name=p.product_name,
                    display_name=p.display_name,
                    price=str(p.price),
                    grant_type=p.grant_type,
                    duration_seconds=str(p.duration_seconds),
                    card_days=str(p.card_days),
                    buy_url=p.buy_url,
                )
                for p in products
            ]
        )
    )


@router.post(BrowserMembershipRouterPath.payment_confirm)
async def confirm_payments(
    req: EmptyRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[PaymentConfirmResponse]:
    """支付对账确认：服务端查 Casdoor 真实状态后幂等入账（防伪造跳转）"""
    casdoor_user = auth.user_name or ""
    if not casdoor_user:
        # 无 Casdoor 用户名无法对账，按空结果返回（不报错，避免正常页面打开被弹错）
        return success_response(PaymentConfirmResponse(granted=[]))
    granted = await MembershipService.confirm_payments(auth.mid, casdoor_user, session)
    await session.commit()
    return success_response(
        PaymentConfirmResponse(
            granted=[
                PaymentGrantedItem(product=g.product, summary=g.summary)
                for g in granted
            ]
        )
    )


class PaymentNotifyRequest(SQLModel):
    """支付完成通知请求（透传 Casdoor Success URL 的跳转参数）

    参数仅作为「去 Casdoor 核验哪笔支付」的线索，入账以服务端 get-payment 为准。
    """

    # owner/name 会被拼进 Casdoor 管理端 URL 路径，字符白名单必须收紧，
    # 否则可构造 ../ 打到 Casdoor 内部其它端点（校验见 parse_casdoor_identifier）。
    transaction_owner: str = Field(
        default="", max_length=100, description="Casdoor 跳转参数 transactionOwner"
    )
    transaction_name: str = Field(
        default="", max_length=100, description="Casdoor 跳转参数 transactionName"
    )


@router.post(BrowserMembershipRouterPath.payment_notify)
async def notify_payment(
    req: PaymentNotifyRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[PaymentConfirmResponse]:
    """支付完成通知：notify-payment 完成交易 + 服务端核验 + 幂等入账（计划书 §3.3）"""
    casdoor_user = auth.user_name or ""
    if not casdoor_user:
        return success_response(PaymentConfirmResponse(granted=[]))
    owner = parse_casdoor_identifier(req.transaction_owner, "transaction_owner")
    name = parse_casdoor_identifier(req.transaction_name, "transaction_name")
    result = await MembershipService.notify_and_grant(
        auth.mid,
        casdoor_user,
        owner,
        name,
        session,
    )
    await session.commit()
    return success_response(
        PaymentConfirmResponse(
            granted=(
                [PaymentGrantedItem(product=result.product, summary=result.summary)]
                if result
                else []
            )
        )
    )
