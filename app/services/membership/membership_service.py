"""会员权益服务 - 时长账户 / 消耗结算 / 签到 / 兑换码 / 月卡 / 使用统计

设计依据：docs/浏览器使用时长与会员权益计划书.md

核心约定：
    - 只有定时任务（SCHEDULE）工作流消耗时长；手动调试 / 手动运行只记统计不扣费；
    - 扣减按「运行实际时长向上取整到分钟」；
    - 余额扣减在事务内行锁（SELECT ... FOR UPDATE）完成，保证并发安全；
    - 服务层方法显式接收 session（依赖注入），不持有全局状态。
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from calendar import monthrange
from datetime import date, datetime, time, timedelta

from loguru import logger
from sqlalchemy import func, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.base.base_sqlmodel import BasePaginationReq, BasePaginationResp
from app.models.common.exceptions.base_exception import (
    InvalidMembershipParamException,
    RedeemCodeAlreadyUsedException,
    RedeemCodeExhaustedException,
    RedeemCodeInvalidException,
    SignInAlreadyTodayException,
    SignMakeupAlreadySignedException,
    SignMakeupCardNotEnoughException,
    SignMakeupNotAllowedException,
    SignRewardTierExchangedException,
    SignRewardTierLockedException,
    SignRewardTierNotFoundException,
)
from app.models.membership.dto import PaymentGrantResult, StoreProductItem
from app.services.membership.casdoor_payment_client import (
    CasdoorPaymentClient,
    CasdoorPaymentPayload,
)
from app.utils.time_util import now_local, today_local
from app.models.database.membership.models import (
    BrowserUsageDailyStat,
    CodeRedemptionRecord,
    DurationLedger,
    LedgerChangeType,
    MonthCardRecord,
    MonthCardSourceEnum,
    PaymentGrantTypeEnum,
    PaymentOrder,
    RedeemCodeTypeEnum,
    RedemptionCode,
    SignInRecord,
    SignRewardExchange,
    SignRewardTierEnum,
    UserDurationAccount,
    UserSignProfile,
)

from app.services.membership.sign_reward_config_service import (
    SignRewardConfigService,
)

# ============ 签到奖励参数（计划书 §13：全部外置到 app/data/sign_in_rewards.json） ============


def membership_log_mask(value: str) -> str:
    """日志脱敏：只保留首字符与长度，避免用户名等 PII 落入日志"""
    if not value:
        return "<empty>"
    return f"{value[0]}***(len={len(value)})"


def build_redeem_summary(code: RedemptionCode) -> str:
    """兑换所得摘要文案（唯一出处，避免 router 与 service 双份实现漂移）"""
    if code.code_type == RedeemCodeTypeEnum.MONTH_CARD:
        return f"月卡 {code.card_days} 天"
    return f"时长 {code.duration_seconds} 秒"


def build_sign_remark(total_sign_days: int, *, is_makeup: bool) -> str:
    """签到流水备注：累计天数 + 是否补签"""
    parts = [f"累计签到 {total_sign_days} 天"]
    if is_makeup:
        parts.append("补签")
    return "每日签到（" + " · ".join(parts) + "）"


class MembershipService:
    """时长 / 月卡 / 签到 / 兑换 统一服务（无状态，全部静态方法）"""

    # ---------- 账户 ----------

    @staticmethod
    async def get_or_create_account(
        mid: int, session: AsyncSession
    ) -> UserDurationAccount:
        """获取（或懒创建）用户时长账户（并发创建撞唯一约束时重读）"""
        account = (
            await session.exec(
                select(UserDurationAccount).where(UserDurationAccount.mid == mid)
            )
        ).first()
        if account is not None:
            return account
        session.add(UserDurationAccount(mid=mid))
        try:
            # SAVEPOINT：仅回滚本次 INSERT，避免把调用方已排队的写一起回滚
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            pass  # 并发首次创建：唯一约束冲突，下方重读已有行
        return (
            await session.exec(
                select(UserDurationAccount).where(UserDurationAccount.mid == mid)
            )
        ).one()

    # ---------- 月卡 ----------

    @staticmethod
    async def get_active_month_card(
        mid: int, session: AsyncSession, now: datetime | None = None
    ) -> MonthCardRecord | None:
        """获取当前生效中的月卡（多张时取最晚到期的一张）"""
        now = now or now_local()
        cards = (
            await session.exec(
                select(MonthCardRecord)
                .where(
                    MonthCardRecord.mid == mid,
                    MonthCardRecord.start_at <= now,
                    MonthCardRecord.expire_at > now,
                )
                .order_by(MonthCardRecord.expire_at.desc())
            )
        ).all()
        return cards[0] if cards else None

    @staticmethod
    async def grant_month_card(
        mid: int,
        days: int,
        session: AsyncSession,
        *,
        source: MonthCardSourceEnum,
        ref_id: str = "",
        remark: str = "",
        now: datetime | None = None,
    ) -> MonthCardRecord:
        """发放月卡：已有生效月卡时自其到期时间顺延，否则立即生效"""
        now = now or now_local()
        active = await MembershipService.get_active_month_card(mid, session, now)
        start_at = active.expire_at if active else now
        card = MonthCardRecord(
            mid=mid,
            source=source,
            start_at=start_at,
            expire_at=start_at + timedelta(days=days),
            ref_id=ref_id,
            remark=remark,
        )
        session.add(card)
        await session.flush()
        return card

    # ---------- 时长发放 / 消耗 ----------

    @staticmethod
    async def grant_duration(
        mid: int,
        seconds: int,
        session: AsyncSession,
        *,
        change_type: LedgerChangeType,
        ref_id: str = "",
        workflow_id: str | None = None,
        run_id: str | None = None,
        browser_id: int | None = None,
        remark: str = "",
    ) -> UserDurationAccount:
        """发放时长（正数），写流水并更新账户汇总"""
        if seconds <= 0:
            raise ValueError(f"grant_duration seconds must be positive: {seconds}")
        account = await MembershipService.get_or_create_account(mid, session)
        # 行锁重读，防止并发丢失更新
        locked = (
            await session.exec(
                select(UserDurationAccount)
                .where(UserDurationAccount.id == account.id)
                .with_for_update()
            )
        ).one()
        locked.balance_seconds += seconds
        locked.total_granted_seconds += seconds
        session.add(
            DurationLedger(
                mid=mid,
                change_seconds=seconds,
                balance_after=locked.balance_seconds,
                change_type=change_type,
                ref_id=ref_id,
                workflow_id=workflow_id,
                run_id=run_id,
                browser_id=browser_id,
                remark=remark,
            )
        )
        session.add(locked)
        await session.flush()
        return locked

    @staticmethod
    async def _charge_run_delta(
        mid: int,
        delta_seconds: int,
        session: AsyncSession,
        *,
        workflow_id: str,
        run_id: str,
        browser_id: int,
        remark: str = "定时任务工作流运行消耗",
    ) -> int:
        """对运行扣减指定秒数（行锁，余额封顶不为负），写 CONSUME 流水

        Returns:
            实际扣减秒数（余额不足时小于 delta）。
        """
        if delta_seconds <= 0:
            return 0
        account = await MembershipService.get_or_create_account(mid, session)
        locked = (
            await session.exec(
                select(UserDurationAccount)
                .where(UserDurationAccount.id == account.id)
                .with_for_update()
            )
        ).one()
        charged = min(delta_seconds, max(locked.balance_seconds, 0))
        if charged <= 0:
            return 0
        locked.balance_seconds -= charged
        locked.total_consumed_seconds += charged
        session.add(
            DurationLedger(
                mid=mid,
                change_seconds=-charged,
                balance_after=locked.balance_seconds,
                change_type=LedgerChangeType.CONSUME,
                workflow_id=workflow_id,
                run_id=run_id,
                browser_id=browser_id,
                remark=remark,
            )
        )
        session.add(locked)
        await session.flush()
        return charged

    @staticmethod
    async def _sum_run_charged(session: AsyncSession, run_id: str) -> int:
        """汇总某次运行已（预）扣的总秒数（按流水聚合）"""
        total = (
            await session.exec(
                select(
                    func.coalesce(func.sum(-DurationLedger.change_seconds), 0)
                ).where(
                    DurationLedger.run_id == run_id,
                    DurationLedger.change_type == LedgerChangeType.CONSUME,
                )
            )
        ).one()
        return int(total or 0)

    @staticmethod
    async def finalize_run(
        mid: int,
        run_seconds: float,
        session: AsyncSession,
        *,
        workflow_id: str,
        run_id: str,
        browser_id: int,
        already_charged: int,
    ) -> int:
        """运行结束的最终结算：目标(向上取整分钟) - 已预扣，差额补扣

        计划书 §8.3：不为负、不冲正（心跳竞态导致的少量多扣接受）。
        月卡生效中不补扣。

        Returns:
            本次补扣秒数。
        """
        run_seconds = max(run_seconds, 0.0)
        if await MembershipService.get_active_month_card(mid, session) is not None:
            return 0
        target_total = math.ceil(run_seconds / 60.0) * 60
        delta = target_total - already_charged
        if delta <= 0:
            return 0
        return await MembershipService._charge_run_delta(
            mid,
            delta,
            session,
            workflow_id=workflow_id,
            run_id=run_id,
            browser_id=browser_id,
            remark="定时任务工作流运行结算（补扣）",
        )

    @staticmethod
    async def check_run_allowed(mid: int, session: AsyncSession) -> bool:
        """定时工作流启动前校验：月卡生效中，或余额 > 0"""
        if await MembershipService.get_active_month_card(mid, session) is not None:
            return True
        account = await MembershipService.get_or_create_account(mid, session)
        return account.balance_seconds > 0

    # ---------- 签到 ----------

    @staticmethod
    def _today_range(now: datetime) -> tuple[datetime, datetime]:
        start = datetime.combine(now.date(), time.min)
        return start, start + timedelta(days=1)

    @staticmethod
    async def sign_in(mid: int, session: AsyncSession) -> SignInRecord:
        """今日签到（幂等：当日重复签到抛业务异常）

        奖励口径见计划书 §13：每天发配置里的固定时长（`daily_reward_seconds`），
        累计天数只用于档位解锁与展示，签到当天不再自动叠加里程碑奖励。
        """
        today = today_local()
        exists = (
            await session.exec(
                select(SignInRecord).where(
                    SignInRecord.mid == mid, SignInRecord.sign_date == today
                )
            )
        ).first()
        if exists is not None:
            raise SignInAlreadyTodayException()

        config = await SignRewardConfigService.get_config()
        # 累计天数要读-改-写，必须行锁（并发签到/补签下的丢失更新）
        profile = await MembershipService.get_or_create_sign_profile(
            mid, session, for_update=True
        )
        total_days = profile.total_sign_days + 1
        reward = config.daily_reward_seconds

        record = SignInRecord(
            mid=mid,
            sign_date=today,
            reward_seconds=reward,
            # 历史字段：保留写入累计天数，前端旧字段仍可读
            continuous_days=total_days,
            is_makeup=False,
            makeup_card_cost=0,
        )
        session.add(record)
        try:
            # 并发双提交由唯一键 (mid, sign_date) 兜底：转成业务码而不是 500。
            # SAVEPOINT 保证仅回滚本次 INSERT，不影响调用方已排队的写。
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            raise SignInAlreadyTodayException()
        profile.total_sign_days = total_days
        await MembershipService.grant_duration(
            mid,
            reward,
            session,
            change_type=LedgerChangeType.SIGN_IN,
            remark=build_sign_remark(total_days, is_makeup=False),
        )
        await session.flush()
        return record

    @staticmethod
    async def makeup_sign_in(
        mid: int, sign_date: date, session: AsyncSession
    ) -> SignInRecord:
        """补签指定日期（消耗 1 张补登卡，奖励同每日签到）

        可补范围由配置 `makeup_window_days` 决定：`0` = 仅限当月已漏签的过去日期；
        `N>0` = 允许补最近 N 天（含跨月）。

        Returns:
            补签产生的 SignInRecord（含当日实际发放奖励）。

        Raises:
            SignMakeupNotAllowedException: 日期不合法 / 超出补签窗口 / 不早于今天；
            SignMakeupAlreadySignedException: 该日期已签到；
            SignMakeupCardNotEnoughException: 补登卡不足。
        """
        today = today_local()
        config = await SignRewardConfigService.get_config()
        window_days = config.makeup_window_days
        if window_days > 0:
            earliest = today - timedelta(days=window_days)
            in_window = earliest <= sign_date < today
        else:
            in_window = (
                sign_date.year == today.year
                and sign_date.month == today.month
                and sign_date < today
            )
        if not in_window:
            raise SignMakeupNotAllowedException()

        exists = (
            await session.exec(
                select(SignInRecord).where(
                    SignInRecord.mid == mid, SignInRecord.sign_date == sign_date
                )
            )
        ).first()
        if exists is not None:
            raise SignMakeupAlreadySignedException()

        # 补登卡扣减 + 累计天数自增都是读-改-写，必须行锁，否则 1 张卡可补签多次
        profile = await MembershipService.get_or_create_sign_profile(
            mid, session, for_update=True
        )
        if profile.makeup_cards < 1:
            raise SignMakeupCardNotEnoughException()

        total_days = profile.total_sign_days + 1
        reward = config.daily_reward_seconds

        record = SignInRecord(
            mid=mid,
            sign_date=sign_date,
            reward_seconds=reward,
            continuous_days=total_days,
            is_makeup=True,
            makeup_card_cost=1,
        )
        session.add(record)
        try:
            # 并发重复补签由唯一键 (mid, sign_date) 兜底
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            raise SignMakeupAlreadySignedException()
        profile.makeup_cards -= 1
        profile.total_sign_days = total_days
        await MembershipService.grant_duration(
            mid,
            reward,
            session,
            change_type=LedgerChangeType.SIGN_IN,
            remark=build_sign_remark(total_days, is_makeup=True),
        )
        await session.flush()
        return record

    @staticmethod
    async def get_or_create_sign_profile(
        mid: int, session: AsyncSession, *, for_update: bool = False
    ) -> UserSignProfile:
        """获取（或懒创建）用户签到档案（并发创建撞唯一约束时重读）

        Args:
            for_update: 需要对其做读-改-写（补登卡扣减、累计天数自增、兑换封顶）
                时必须置 True 加行锁，否则并发下会丢失更新。
        """
        stmt = select(UserSignProfile).where(UserSignProfile.mid == mid)
        if for_update:
            stmt = stmt.with_for_update()
        profile = (await session.exec(stmt)).first()
        if profile is not None:
            return profile

        session.add(UserSignProfile(mid=mid))
        try:
            # SAVEPOINT：仅回滚本次 INSERT，避免误伤调用方已排队的写
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            pass  # 并发首次创建：唯一约束冲突，下方重读已有行

        # MySQL REPEATABLE READ 下必须重新加行锁读取，否则拿到的是快照行
        retry_stmt = select(UserSignProfile).where(UserSignProfile.mid == mid)
        if for_update:
            retry_stmt = retry_stmt.with_for_update()
        return (await session.exec(retry_stmt)).one()

    @staticmethod
    async def list_sign_reward_exchanges(
        mid: int, session: AsyncSession
    ) -> list[SignRewardExchange]:
        """已兑换的奖励档位记录"""
        items = (
            await session.exec(
                select(SignRewardExchange)
                .where(SignRewardExchange.mid == mid)
                .order_by(SignRewardExchange.id.asc())
            )
        ).all()
        return list(items)

    @staticmethod
    async def exchange_sign_reward(
        mid: int, tier: SignRewardTierEnum, session: AsyncSession
    ) -> SignRewardExchange:
        """兑换奖励档位（每档每用户仅一次；奖励与门槛取自 JSON 配置）

        补登卡发放按配置 `makeup_card_max` 封顶。

        Raises:
            SignRewardTierLockedException: 累计签到天数未达门槛；
            SignRewardTierExchangedException: 该档位已兑换过；
            SignRewardTierNotFoundException: 配置中缺少该档位。
        """
        app_config = await SignRewardConfigService.get_config()
        tier_config = app_config.get_tier(tier.value)
        if tier_config is None:
            raise SignRewardTierNotFoundException(tier.value)
        # 补登卡发放要按上限封顶（读-改-写），必须行锁防并发超额发放
        profile = await MembershipService.get_or_create_sign_profile(
            mid, session, for_update=True
        )
        if profile.total_sign_days < tier_config.days:
            raise SignRewardTierLockedException(tier_config.days)
        exchanged = (
            await session.exec(
                select(SignRewardExchange).where(
                    SignRewardExchange.mid == mid, SignRewardExchange.tier == tier
                )
            )
        ).first()
        if exchanged is not None:
            raise SignRewardTierExchangedException()

        # 补登卡按上限封顶发放
        granted_cards = min(
            tier_config.makeup_cards,
            max(0, app_config.makeup_card_max - profile.makeup_cards),
        )
        record = SignRewardExchange(
            mid=mid,
            tier=tier,
            reward_seconds=tier_config.reward_seconds,
            makeup_cards=granted_cards,
            total_sign_days=profile.total_sign_days,
        )
        session.add(record)
        try:
            # 并发重复兑换由唯一键 (mid, tier) 兜底：转成业务码而不是 500
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            raise SignRewardTierExchangedException()
        if tier_config.reward_seconds > 0:
            await MembershipService.grant_duration(
                mid,
                tier_config.reward_seconds,
                session,
                change_type=LedgerChangeType.ACTIVITY,
                remark=f"签到奖励兑换：{tier_config.tier} 档",
            )
        profile.makeup_cards += granted_cards
        await session.flush()
        return record

    @staticmethod
    async def list_makeupable_dates(
        mid: int, month: date, session: AsyncSession
    ) -> list[date]:
        """当月（或补签窗口内）可补登的日期列表 = 未签到且早于今天"""
        today = today_local()
        records = await MembershipService.list_sign_in_records(mid, session, month)
        signed = {r.sign_date for r in records}
        config = await SignRewardConfigService.get_config()
        if config.makeup_window_days > 0:
            # 跨月窗口：窗口起点之前的月份也要查一次
            start = today - timedelta(days=config.makeup_window_days)
            extra_month = start.replace(day=1)
            if extra_month != month:
                extra_records = await MembershipService.list_sign_in_records(
                    mid, session, extra_month
                )
                signed |= {r.sign_date for r in extra_records}
        days = monthrange(month.year, month.month)[1]
        return [
            month.replace(day=n)
            for n in range(1, days + 1)
            if (d := month.replace(day=n)) not in signed and d < today
        ]

    @staticmethod
    async def is_signed_today(mid: int, session: AsyncSession) -> bool:
        record = (
            await session.exec(
                select(SignInRecord).where(
                    SignInRecord.mid == mid,
                    SignInRecord.sign_date == today_local(),
                )
            )
        ).first()
        return record is not None

    @staticmethod
    async def list_sign_in_records(
        mid: int, session: AsyncSession, month: date
    ) -> list[SignInRecord]:
        """查询指定月份（按自然月边界）的签到记录，按日期升序"""
        first_day = month.replace(day=1)
        last_day = first_day.replace(day=monthrange(first_day.year, first_day.month)[1])
        records = (
            await session.exec(
                select(SignInRecord)
                .where(
                    SignInRecord.mid == mid,
                    SignInRecord.sign_date >= first_day,
                    SignInRecord.sign_date <= last_day,
                )
                .order_by(SignInRecord.sign_date.asc())
            )
        ).all()
        return list(records)

    # ---------- 兑换码 ----------

    @staticmethod
    async def redeem_code(mid: int, code: str, session: AsyncSession) -> RedemptionCode:
        """兑换码兑换（时长卡直加余额 / 月卡按天顺延）

        Returns:
            兑换成功的 RedemptionCode（含面值信息，供上层组装响应）。
        """
        code = (code or "").strip()
        if not code:
            raise RedeemCodeInvalidException()

        # 防重复兑换（同一用户同一码）
        redeemed = (
            await session.exec(
                select(CodeRedemptionRecord).where(
                    CodeRedemptionRecord.code == code,
                    CodeRedemptionRecord.mid == mid,
                )
            )
        ).first()
        if redeemed is not None:
            raise RedeemCodeAlreadyUsedException()

        # 行锁读取码，防止并发超发
        code_model = (
            await session.exec(
                select(RedemptionCode)
                .where(RedemptionCode.code == code)
                .with_for_update()
            )
        ).first()
        if (
            code_model is None
            or not code_model.is_enabled
            or (
                code_model.expire_at is not None and code_model.expire_at <= now_local()
            )
        ):
            raise RedeemCodeInvalidException()
        if code_model.used_count >= code_model.max_uses:
            raise RedeemCodeExhaustedException()

        try:
            # used_count 自增与兑换流水必须在同一 SAVEPOINT 内：并发重复兑换时
            # 唯一键 (code, mid) 冲突会让 used_count 一并回滚，不会超发。
            async with session.begin_nested():
                code_model.used_count += 1
                session.add(code_model)
                session.add(
                    CodeRedemptionRecord(
                        code=code,
                        mid=mid,
                        reward_summary=build_redeem_summary(code_model),
                    )
                )
                await session.flush()
        except IntegrityError:
            raise RedeemCodeAlreadyUsedException()

        if code_model.code_type == RedeemCodeTypeEnum.MONTH_CARD:
            await MembershipService.grant_month_card(
                mid,
                code_model.card_days,
                session,
                source=MonthCardSourceEnum.REDEEM,
                ref_id=code,
                remark="兑换码兑换月卡",
            )
        else:
            await MembershipService.grant_duration(
                mid,
                code_model.duration_seconds,
                session,
                change_type=LedgerChangeType.REDEEM,
                ref_id=code,
                remark="兑换码兑换时长",
            )
        await session.flush()
        return code_model

    # ---------- 兑换码生成（管理端） ----------

    # 无歧义字符集（去除 I / L / O / U / 0 / 1，避免人工转录混淆）
    _CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
    # 生成 / 兑换的参数边界（防超发与管理端误操作）
    _MAX_CODE_BATCH = 500  # 单次批量生成上限
    _MAX_CODE_USES = 100_000  # 单码最大兑换次数上限
    _MAX_CODE_DURATION_SECONDS = 365 * 24 * 3600  # 时长卡面值上限（约 1 年）
    _MAX_CODE_CARD_DAYS = 3 * 365  # 月卡天数上限

    @staticmethod
    def _generate_one_code() -> str:
        """生成单个兑换码：RPA-<12位无歧义大写>"""
        import secrets

        body = "".join(
            secrets.choice(MembershipService._CODE_ALPHABET) for _ in range(12)
        )
        return f"RPA-{body}"

    @staticmethod
    async def generate_codes(
        session: AsyncSession,
        *,
        code_type: RedeemCodeTypeEnum,
        count: int,
        max_uses: int = 1,
        duration_seconds: int = 0,
        card_days: int = 0,
        expire_at: datetime | None = None,
        batch_no: str = "",
        remark: str = "",
    ) -> list[RedemptionCode]:
        """批量生成兑换码（撞唯一键自动重生成，最多重试 5 轮）

        仅时长卡 / 月卡两类；调用方负责写管理审计日志。
        """
        import secrets

        # 参数边界统一在服务端卡死（路由层只负责把 str 转 int）
        if not 1 <= count <= MembershipService._MAX_CODE_BATCH:
            raise InvalidMembershipParamException(
                f"生成数量 count 需在 1~{MembershipService._MAX_CODE_BATCH} 之间，当前 {count}"
            )
        if not 1 <= max_uses <= MembershipService._MAX_CODE_USES:
            raise InvalidMembershipParamException(
                f"最大兑换次数 max_uses 需在 1~{MembershipService._MAX_CODE_USES} 之间，"
                f"当前 {max_uses}"
            )
        if duration_seconds < 0 or card_days < 0:
            raise InvalidMembershipParamException("面值不能为负数")
        if (
            code_type == RedeemCodeTypeEnum.MONTH_CARD
            and not 1 <= card_days <= MembershipService._MAX_CODE_CARD_DAYS
        ):
            raise InvalidMembershipParamException(
                f"月卡天数 card_days 需在 1~{MembershipService._MAX_CODE_CARD_DAYS} 之间，"
                f"当前 {card_days}"
            )
        if (
            code_type == RedeemCodeTypeEnum.DURATION
            and not 1
            <= duration_seconds
            <= MembershipService._MAX_CODE_DURATION_SECONDS
        ):
            raise InvalidMembershipParamException(
                f"时长卡面值 duration_seconds 需在 1~"
                f"{MembershipService._MAX_CODE_DURATION_SECONDS} 秒之间，"
                f"当前 {duration_seconds}"
            )

        created: list[RedemptionCode] = []
        remaining = count
        for _attempt in range(5):  # 撞唯一键重试轮数
            if remaining <= 0:
                break
            candidates = [
                MembershipService._generate_one_code() for _ in range(remaining)
            ]
            existing = (
                await session.exec(
                    select(RedemptionCode.code).where(
                        RedemptionCode.code.in_(candidates)
                    )
                )
            ).all()
            taken = set(existing)
            for code in candidates:
                if code in taken:
                    continue  # 已存在，下一轮重生成
                model = RedemptionCode(
                    code=code,
                    code_type=code_type,
                    duration_seconds=duration_seconds,
                    card_days=card_days,
                    max_uses=max_uses,
                    expire_at=expire_at,
                    batch_no=batch_no or secrets.token_hex(4),
                    remark=remark,
                )
                session.add(model)
                created.append(model)
                remaining -= 1
            if created:
                await session.flush()
        if remaining > 0:
            raise InvalidMembershipParamException(
                f"生成兑换码连续撞唯一键，请重试（已生成 {len(created)}/{count}）"
            )
        return created

    @staticmethod
    async def list_codes(
        session: AsyncSession,
        *,
        batch_no: str | None = None,
        code_type: RedeemCodeTypeEnum | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> BasePaginationResp[RedemptionCode]:
        """分页查询兑换码（管理端，时间倒序）"""
        page, per_page = MembershipService._normalize_pagination(page, per_page)
        where = []
        if batch_no:
            where.append(RedemptionCode.batch_no == batch_no)
        if code_type is not None:
            where.append(RedemptionCode.code_type == code_type)
        count_stmt = select(func.count()).select_from(RedemptionCode)
        stmt = select(RedemptionCode)
        if where:
            count_stmt = count_stmt.where(*where)
            stmt = stmt.where(*where)
        total = (await session.exec(count_stmt)).one()
        items = (
            await session.exec(
                stmt.order_by(RedemptionCode.created_at.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        ).all()
        return BasePaginationResp[RedemptionCode](
            page=page, per_page=per_page, total=total, items=list(items)
        )

    # ---------- 支付对账入账（计划书 §3.3 方案A：商品名约定解析） ----------

    # 商品名约定（大小写不敏感，模式可出现在名称任意位置）：
    #   duration-<N>m  → 时长 N 分钟；duration-<N>h → 时长 N 小时
    #   monthcard-<N>d → 月卡 N 天（一个月=31天）
    _DURATION_PATTERN = re.compile(r"duration[-_](\d+)([mh])", re.IGNORECASE)
    _MONTHCARD_PATTERN = re.compile(r"month[_-]?card[-_](\d+)d", re.IGNORECASE)

    # 商品名解析出的权益必须在合理区间内（防误配 / 恶意命名导致超发）
    _MIN_GRANT_SECONDS = 60
    _MAX_GRANT_SECONDS = 365 * 24 * 3600  # 单次入账时长上限（约 1 年）
    _MAX_GRANT_CARD_DAYS = 3 * 365  # 单次入账月卡天数上限

    @staticmethod
    def parse_product_spec(
        product_name: str,
    ) -> tuple[PaymentGrantTypeEnum, int, int] | None:
        """解析商品名中的权益规格（解析结果必须在合理区间内）

        Returns:
            (grant_type, duration_seconds, card_days)；无法解析或越界返回 None。
        """
        name = product_name or ""
        if m := MembershipService._MONTHCARD_PATTERN.search(name):
            days = max(int(m.group(1)), 1)
            if days > MembershipService._MAX_GRANT_CARD_DAYS:
                logger.warning(
                    f"[Membership] 商品名解析出越界的月卡天数，拒入账: "
                    f"{product_name} -> {days}d"
                )
                return None
            return PaymentGrantTypeEnum.MONTH_CARD, 0, days
        if m := MembershipService._DURATION_PATTERN.search(name):
            value = int(m.group(1))
            seconds = value * (3600 if m.group(2).lower() == "h" else 60)
            if (
                not MembershipService._MIN_GRANT_SECONDS
                <= seconds
                <= MembershipService._MAX_GRANT_SECONDS
            ):
                logger.warning(
                    f"[Membership] 商品名解析出越界的时长，拒入账: "
                    f"{product_name} -> {seconds}s"
                )
                return None
            return PaymentGrantTypeEnum.DURATION, seconds, 0
        return None

    @staticmethod
    async def list_store_products() -> list[StoreProductItem]:
        """从 Casdoor 拉取可售商品（实时，解析出规格的才返回）

        Returns:
            可售商品列表；``buy_url`` 为空表示未启用 Casdoor 站点配置。

        Raises:
            PaymentVerifyFailedException: Casdoor 未配置 / 查询失败。
        """
        from app.config import settings
        from app.models.common.exceptions.base_exception import (
            PaymentVerifyFailedException,
        )

        try:
            products = await CasdoorPaymentClient.list_products()
        except RuntimeError as exc:
            logger.error(f"[Membership] 拉取支付商品失败: {exc}")
            raise PaymentVerifyFailedException() from exc

        base = (
            settings.casdoor_endpoint.rstrip("/") if settings.casdoor_endpoint else ""
        )
        result: list[StoreProductItem] = []
        for product in products:
            name = product.name
            if not name:
                continue
            spec = MembershipService.parse_product_spec(name)
            if spec is None:
                continue  # 不符合命名约定的商品不对外展示
            grant_type, seconds, days = spec
            result.append(
                StoreProductItem(
                    product_name=name,
                    display_name=product.display_name or name,
                    price=product.price,
                    grant_type=grant_type.value,
                    duration_seconds=seconds,
                    card_days=days,
                    buy_url=f"{base}/buy/{name}" if base else "",
                )
            )
        return result

    @staticmethod
    async def _grant_paid_payment(
        mid: int,
        casdoor_user: str,
        payment: CasdoorPaymentPayload,
        session: AsyncSession,
    ) -> PaymentGrantResult | None:
        """对单笔已支付（Paid）的 Casdoor 支付做幂等入账

        校验：支付归属当前用户；商品名能解析出有效权益。
        入账：先插 paymentorder（唯一键防重放）再 grant。

        Returns:
            入账结果；不满足条件（含已入账）返回 None。
        """
        payment_name = payment.payment_name
        product_name = payment.product_name
        if not payment_name or not product_name:
            return None
        # 支付归属校验：他人支付单不得入账到当前用户（大小写归一，避免用户名变更误拒）
        if payment.user.strip().lower() != casdoor_user.strip().lower():
            logger.warning(
                f"[Membership] 支付单归属不符，拒绝入账: "
                f"payment_user={membership_log_mask(payment.user)}, "
                f"payment={payment_name}"
            )
            return None

        # 幂等：已入账的支付直接跳过
        exists = (
            await session.exec(
                select(PaymentOrder).where(PaymentOrder.payment_name == payment_name)
            )
        ).first()
        if exists is not None:
            return None

        # 商品名约定解析（方案A）：解析失败拒入账并告警
        spec = MembershipService.parse_product_spec(product_name)
        if spec is None:
            logger.warning(
                f"[Membership] 商品名不符合约定，无法解析权益，拒入账: {product_name}, "
                f"payment={payment_name}（约定见计划书 §3.3）"
            )
            return None
        grant_type, seconds, days = spec

        # 幂等入账：先插记录（唯一键防重放），再入账
        order = PaymentOrder(
            payment_name=payment_name,
            casdoor_user=casdoor_user,
            mid=mid,
            product_name=product_name,
            grant_type=grant_type,
            duration_seconds=seconds,
            card_days=days,
            price=payment.price,
        )
        session.add(order)
        try:
            # SAVEPOINT：并发对账重复入账时仅回滚本笔，
            # 不会把 confirm_payments 里前面已入账的笔一起回滚。
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            logger.info(f"[Membership] 支付单已入账，幂等跳过: payment={payment_name}")
            return None

        if grant_type == PaymentGrantTypeEnum.MONTH_CARD:
            await MembershipService.grant_month_card(
                mid,
                days,
                session,
                source=MonthCardSourceEnum.ACTIVITY,
                ref_id=payment_name,
                remark=f"购买月卡商品 {product_name}",
            )
            return PaymentGrantResult(
                product=product_name,
                summary=f"月卡已开通：{days} 天",
            )
        await MembershipService.grant_duration(
            mid,
            seconds,
            session,
            change_type=LedgerChangeType.ACTIVITY,
            ref_id=payment_name,
            remark=f"购买时长商品 {product_name}",
        )
        return PaymentGrantResult(
            product=product_name,
            summary=f"时长已到账：{seconds} 秒",
        )

    @staticmethod
    async def notify_and_grant(
        mid: int,
        casdoor_user: str,
        transaction_owner: str,
        transaction_name: str,
        session: AsyncSession,
    ) -> PaymentGrantResult | None:
        """Success URL 跳回后的支付完成流程（计划书 §3.3）

        1. get-payment 服务端先核验归属：**确认属于当前用户才允许推进交易**，
           否则任何人凭 owner/name 就能拿应用凭证替他人完成交易（越权 + 副作用）；
        2. notify-payment 完成交易（配置 Success URL 后 Casdoor 不自动完成）；
        3. 重新核验真实状态（前端参数仅作线索），state=Paid → 幂等入账。

        Raises:
            PaymentVerifyFailedException: 归属不符 / notify 失败 / 支付未完成。
        """
        from app.models.common.exceptions.base_exception import (
            PaymentVerifyFailedException,
        )

        if not transaction_owner or not transaction_name:
            raise PaymentVerifyFailedException()
        try:
            payment = await CasdoorPaymentClient.get_payment(
                transaction_owner, transaction_name
            )
        except RuntimeError as exc:
            logger.error(f"[Membership] 支付核验失败: {exc}")
            raise PaymentVerifyFailedException() from exc
        if payment is None:
            raise PaymentVerifyFailedException()

        # 归属校验必须前置：notify-payment 会真实推进 Casdoor 交易状态，
        # 归属不符时绝不能代他人完成交易。
        if payment.user.strip().lower() != casdoor_user.strip().lower():
            logger.warning(
                f"[Membership] 支付单归属不符，拒绝推进交易: "
                f"payment_user={membership_log_mask(payment.user)}, "
                f"login_user={membership_log_mask(casdoor_user)}, "
                f"payment={payment.payment_name}"
            )
            raise PaymentVerifyFailedException()

        try:
            await CasdoorPaymentClient.notify_payment(
                transaction_owner, transaction_name
            )
            payment = await CasdoorPaymentClient.get_payment(
                transaction_owner, transaction_name
            )
        except RuntimeError as exc:
            logger.error(f"[Membership] 支付核验失败: {exc}")
            raise PaymentVerifyFailedException() from exc
        if payment is None:
            raise PaymentVerifyFailedException()
        if not payment.is_paid:
            logger.warning(
                f"[Membership] 支付未完成，拒绝入账: "
                f"{transaction_owner}/{transaction_name}, state={payment.state}"
            )
            raise PaymentVerifyFailedException()
        return await MembershipService._grant_paid_payment(
            mid, casdoor_user, payment, session
        )

    @staticmethod
    async def confirm_payments(
        mid: int,
        casdoor_user: str,
        session: AsyncSession,
    ) -> list[PaymentGrantResult]:
        """兜底对账：按用户拉取支付列表，补入账（计划书 §3.3）

        每笔入账在独立 SAVEPOINT 内完成：单笔失败只记录告警并继续，
        避免把本轮前面已入账的结果整体回滚。

        Returns:
            本次入账结果列表（空列表=无新入账）。
        """
        payments = await CasdoorPaymentClient.list_user_payments(casdoor_user)
        granted: list[PaymentGrantResult] = []
        for payment in payments:
            if not payment.is_paid:
                continue
            try:
                async with session.begin_nested():
                    result = await MembershipService._grant_paid_payment(
                        mid, casdoor_user, payment, session
                    )
                    await session.flush()
            except Exception as exc:  # noqa: BLE001 - 单笔失败不影响其余订单
                logger.error(
                    f"[Membership] 单笔支付入账失败，跳过: "
                    f"payment={payment.payment_name}, {exc}"
                )
                continue
            if result is not None:
                granted.append(result)
        return granted

    # ---------- 查询 ----------

    # 分页每页上限（防超大 per_page 拖库）
    _MAX_PER_PAGE = 200
    # 使用统计查询的最大日期跨度（防超大区间拖库）
    _MAX_USAGE_RANGE_DAYS = 366
    # 计费心跳单轮处理的最大运行数（防超大结果集 + 长事务）
    _HEARTBEAT_BATCH = 200

    @staticmethod
    def _normalize_pagination(page: int, per_page: int) -> tuple[int, int]:
        """分页参数兜底：页码 >= 1、每页条数限制在 [1, _MAX_PER_PAGE]

        公共分页模型未对 ``page`` / ``per_page`` 施加约束，这里统一兜住两类 500：
        负 offset 触发 SQL 语法错、per_page=0 让响应 computed_field ``pages`` 除零。
        """
        return max(page, 1), min(max(per_page, 1), MembershipService._MAX_PER_PAGE)

    @staticmethod
    async def list_ledger(
        mid: int,
        session: AsyncSession,
        req: BasePaginationReq,
        *,
        change_types: Sequence[LedgerChangeType] | None = None,
    ) -> BasePaginationResp[DurationLedger]:
        """分页查询时长流水（时间倒序）

        change_types 为空表示不过滤（返回全部变动类型）；前端「仅消耗」传
        `(LedgerChangeType.CONSUME,)`，「仅获得」传全部正数类型。
        """
        page, per_page = MembershipService._normalize_pagination(req.page, req.per_page)
        where = [DurationLedger.mid == mid]
        if change_types:
            where.append(DurationLedger.change_type.in_(list(change_types)))
        total = (
            await session.exec(
                select(func.count()).select_from(DurationLedger).where(*where)
            )
        ).one()
        items = (
            await session.exec(
                select(DurationLedger)
                .where(*where)
                .order_by(DurationLedger.id.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        ).all()
        return BasePaginationResp[DurationLedger](
            page=page, per_page=per_page, total=total, items=list(items)
        )

    @staticmethod
    async def record_usage(
        mid: int,
        browser_id: int,
        seconds: float,
        session: AsyncSession,
        *,
        is_workflow: bool,
        is_scheduled: bool,
        now: datetime | None = None,
    ) -> None:
        """累计使用时长日统计（手动/定时都记录，只有定时计费）

        统计口径（计划书 §2）：
            - workflow_seconds：工作流运行时长（定时/手动均计入）；
            - manual_seconds：手动调试会话时长（不计费口径）。
        """
        seconds = max(seconds, 0.0)
        if seconds <= 0:
            return
        if not browser_id or browser_id <= 0:
            # 无有效浏览器（如会话丢失的兜底运行），不产出统计行
            logger.debug(f"[Membership] record_usage 跳过无效 browser_id: {browser_id}")
            return
        now = now or now_local()
        stat_date = now.date()
        amount = math.ceil(seconds)
        if amount <= 0:
            return

        # 原子自增：多路并发（多实例心跳 / 同一浏览器多个工作流）场景下，
        # Python 侧「读出来 + 再写回」会静默丢失更新，必须用 SQL 自增。
        row_filter = (
            BrowserUsageDailyStat.mid == mid,
            BrowserUsageDailyStat.stat_date == stat_date,
            BrowserUsageDailyStat.browser_id == browser_id,
        )
        if is_workflow:
            stmt = (
                sa_update(BrowserUsageDailyStat)
                .where(*row_filter)
                .values(
                    workflow_seconds=BrowserUsageDailyStat.workflow_seconds + amount,
                    workflow_run_count=(
                        BrowserUsageDailyStat.workflow_run_count + 1
                        if is_scheduled
                        else BrowserUsageDailyStat.workflow_run_count
                    ),
                )
            )
        else:
            stmt = (
                sa_update(BrowserUsageDailyStat)
                .where(*row_filter)
                .values(manual_seconds=BrowserUsageDailyStat.manual_seconds + amount)
            )

        # 最多两轮：第一轮 UPDATE（命中即返回），未命中则 INSERT，
        # 并发插入撞唯一键时第二轮再走 UPDATE。
        for _attempt in range(2):
            result = await session.exec(stmt)
            if getattr(result, "rowcount", 0):
                return
            new_row = BrowserUsageDailyStat(
                mid=mid, stat_date=stat_date, browser_id=browser_id
            )
            if is_workflow:
                new_row.workflow_seconds = amount
                new_row.workflow_run_count = 1 if is_scheduled else 0
            else:
                new_row.manual_seconds = amount
            try:
                async with session.begin_nested():
                    session.add(new_row)
                    await session.flush()
                return
            except IntegrityError:
                continue  # 并发已插入，下一轮走 UPDATE
        logger.error(
            f"[Membership] 使用统计写入失败（并发冲突，已重试）: "
            f"mid={mid}, browser={browser_id}, date={stat_date}"
        )

    @staticmethod
    async def list_usage_stats(
        mid: int,
        session: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        browser_id: int | None = None,
    ) -> list[BrowserUsageDailyStat]:
        """按日期区间查询使用日统计（升序）

        区间跨度由路由层按 ``_MAX_USAGE_RANGE_DAYS`` 校验，这里再兜一层结果上限，
        防止超大区间把整张统计表拉进内存。
        """
        where = [
            BrowserUsageDailyStat.mid == mid,
            BrowserUsageDailyStat.stat_date >= start_date,
            BrowserUsageDailyStat.stat_date <= end_date,
        ]
        if browser_id is not None:
            where.append(BrowserUsageDailyStat.browser_id == browser_id)
        items = (
            await session.exec(
                select(BrowserUsageDailyStat)
                .where(*where)
                .order_by(BrowserUsageDailyStat.stat_date.asc())
                .limit(MembershipService._MAX_USAGE_RANGE_DAYS)
            )
        ).all()
        return list(items)

    # ---------- 工作流计费接入（workflow_runner / 后台心跳调用） ----------

    @staticmethod
    async def settle_workflow_run(
        mid: int,
        browser_id: int,
        run_seconds: float,
        *,
        workflow_id: str,
        run_id: str,
        is_scheduled: bool,
    ) -> int | None:
        """运行结束后的独立事务结算：补扣 + 日统计 + 幂等标记，异常只告警不打断主流程

        幂等：``WorkflowRunRecord.settled_seconds`` 非 None 时跳过（心跳兜底已结算）。

        Returns:
            本次补扣秒数（0 = 月卡免扣 / 无需补扣 / 已结算过）。
        """
        from app.models.database.workflow.models import WorkflowRunRecord
        from app.utils.depends.session_manager import DatabaseSessionManager

        try:
            async with DatabaseSessionManager.async_session() as session:
                # 行锁 run 行：与计费心跳互斥（统一「先 run 后账户」锁序，见计划书 §8.6）
                run = (
                    await session.exec(
                        select(WorkflowRunRecord)
                        .where(WorkflowRunRecord.run_id == run_id)
                        .with_for_update()
                    )
                ).first()
                if run is not None and run.settled_seconds is not None:
                    return 0  # 已结算（崩溃兜底路径），幂等跳过

                already_charged = await MembershipService._sum_run_charged(
                    session, run_id
                )
                # 只有定时任务扣费；手动调试 / 手动运行仅记使用统计（计划书 §3.1）
                charged = 0
                if is_scheduled:
                    charged = await MembershipService.finalize_run(
                        mid,
                        run_seconds,
                        session,
                        workflow_id=workflow_id,
                        run_id=run_id,
                        browser_id=browser_id,
                        already_charged=already_charged,
                    )
                await MembershipService.record_usage(
                    mid,
                    browser_id,
                    run_seconds,
                    session,
                    is_workflow=True,
                    is_scheduled=is_scheduled,
                )
                if run is not None:
                    # 已结算标记 = 本次运行累计被扣总量（心跳预扣 + 本次补扣）
                    run.settled_seconds = already_charged + charged
                    session.add(run)
                await session.commit()
                return charged
        except Exception as exc:  # noqa: BLE001 - 结算失败绝不打断工作流主流程
            logger.error(
                f"[Membership] 工作流运行结算失败: mid={mid}, run={run_id}, {exc}"
            )
            return None

    @staticmethod
    async def run_billing_heartbeat() -> None:
        """计费心跳（APScheduler 周期任务，见计划书 §8.1 / §8.2）

        扫描 RUNNING 且 SCHEDULE 触发的运行记录：
        1. 会话仍在运行：无月卡则按墙钟预扣一个心跳周期；余额耗尽 → 熔断关闭会话；
        2. 会话已不存在（进程重启 / 会话丢失）：按 now-started_at 兜底整分钟扣减、
           标记 settled_seconds、运行记录置失败。
        全部异常收敛，绝不打断调度器。
        """
        from app.models.database.workflow.models import (
            WorkflowRunRecord,
            WorkflowRunStatusEnum,
            WorkflowRunTriggerEnum,
        )
        from app.utils.depends.session_manager import DatabaseSessionManager

        now = now_local()
        try:
            async with DatabaseSessionManager.async_session() as session:
                running = (
                    await session.exec(
                        select(WorkflowRunRecord.run_id)
                        .where(
                            WorkflowRunRecord.status == WorkflowRunStatusEnum.RUNNING,
                            WorkflowRunRecord.trigger_source
                            == WorkflowRunTriggerEnum.SCHEDULE,
                            # 已结算（含兜底结算）的运行不再进入心跳，避免锁竞争
                            WorkflowRunRecord.settled_seconds.is_(None),
                        )
                        .order_by(WorkflowRunRecord.started_at.asc())
                        .limit(MembershipService._HEARTBEAT_BATCH)
                    )
                ).all()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[Membership] 计费心跳读取运行记录失败: {exc}")
            return

        for run_id in running:
            try:
                await MembershipService._heartbeat_one(str(run_id), now)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"[Membership] 心跳处理单条运行失败: run={run_id}, {exc}"
                )

    @staticmethod
    async def _heartbeat_one(run_id: str, now: datetime) -> None:
        """处理单条 RUNNING 运行的心跳（预扣 / 熔断 / 崩溃兜底）

        注意：使用统计（record_usage）只在兜底结算路径记录；
        正常运行的统计由 runner 最终结算统一记录，避免重复累加。
        """
        from sqlalchemy import update

        from app.models.database.workflow.models import (
            WorkflowRunRecord,
            WorkflowRunStatusEnum,
        )
        from app.utils.depends.session_manager import DatabaseSessionManager

        async with DatabaseSessionManager.async_session() as session:
            # 行锁重读当前行：与最终结算互斥（统一「先 run 后账户」锁序，见计划书 §8.6）
            current = (
                await session.exec(
                    select(WorkflowRunRecord)
                    .where(WorkflowRunRecord.run_id == run_id)
                    .with_for_update()
                )
            ).first()
            if (
                current is None
                or current.status != WorkflowRunStatusEnum.RUNNING
                or current.settled_seconds is not None  # 已兜底结算过（幂等）
            ):
                return

            mid = int(current.mid)
            browser_id = int(current.browser_id) if current.browser_id else 0
            if browser_id <= 0:
                # 运行记录缺少有效浏览器，无法定位会话也无法统计，跳过心跳
                logger.warning(
                    f"[Membership] 运行记录缺少 browser_id，跳过心跳: run={current.run_id}"
                )
                return

            # 会话是否仍存活（进程重启 / 会话丢失 → 崩溃兜底结算）
            entry = None
            try:
                from app.services.RPA_browser.session.live_service import live_service

                entry = live_service.get_browser_session_entry(mid, browser_id)
            except Exception:  # noqa: BLE001 - 会话不存在
                entry = None

            already_charged = 0
            run_seconds = (now - current.started_at).total_seconds()

            if entry is None:
                # 崩溃兜底：整分钟扣减 + 幂等标记 + 置失败（含使用统计）
                already_charged = await MembershipService._sum_run_charged(
                    session, current.run_id
                )
                target_total = math.ceil(run_seconds / 60.0) * 60
                delta = max(target_total - already_charged, 0)
                charged = await MembershipService._charge_run_delta(
                    mid,
                    delta,
                    session,
                    workflow_id=current.workflow_id,
                    run_id=current.run_id,
                    browser_id=browser_id,
                    remark="定时任务运行会话丢失，兜底结算",
                )
                current.settled_seconds = already_charged + charged
                current.status = WorkflowRunStatusEnum.FAILED
                current.error_message = "会话丢失（进程重启/异常），已兜底结算时长"
                current.finished_at = now
                current.duration_ms = run_seconds * 1000
                session.add(current)
                await MembershipService.record_usage(
                    mid,
                    browser_id,
                    run_seconds,
                    session,
                    is_workflow=True,
                    is_scheduled=True,
                    now=now,
                )
                await session.commit()
                logger.warning(
                    f"[Membership] 崩溃兜底结算完成: run={current.run_id}, "
                    f"charged={charged}s"
                )
                return

            # 会话仍存活
            if await MembershipService.get_active_month_card(mid, session) is not None:
                return  # 月卡免扣

            balance = (
                await MembershipService.get_or_create_account(mid, session)
            ).balance_seconds
            if balance <= 0:
                # 熔断：标记失败 + 关闭会话（执行线程随后自然失败退出，
                # 其最终结算仍会按实际时长补扣/幂等收尾）
                await session.exec(
                    update(WorkflowRunRecord)
                    .where(WorkflowRunRecord.run_id == current.run_id)
                    .values(
                        status=WorkflowRunStatusEnum.FAILED,
                        error_message="时长余额已用尽，定时任务已熔断关闭",
                        finished_at=now,
                    )
                )
                await session.commit()
                try:
                    from app.services.RPA_browser.session.live_service import (
                        live_service,
                    )

                    await live_service.release_browser_session(mid, browser_id)
                    logger.warning(
                        f"[Membership] 余额耗尽熔断: mid={mid}, "
                        f"browser={browser_id}, run={current.run_id}"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"[Membership] 熔断关闭会话失败: {exc}")
                return

            # 差额式预扣（幂等）：只补齐到「当前墙钟分钟」的目标扣费额度。
            # 相比「固定扣一个 interval」，可同时消除两类错误：
            #   1) 多实例各跑一轮心跳 → 同一个 run 被扣 N 倍；
            #   2) 短任务刚启动就被扫到 → 白扣一整个心跳周期且事后不冲正。
            # 注意：心跳不设置 settled_seconds —— 该字段只由最终结算 / 崩溃兜底
            # 写入（幂等标记），否则会把 runner 的最终结算整个跳过。
            already_charged = await MembershipService._sum_run_charged(
                session, current.run_id
            )
            target_total = math.ceil(run_seconds / 60.0) * 60
            delta = target_total - already_charged
            if delta > 0:
                await MembershipService._charge_run_delta(
                    mid,
                    delta,
                    session,
                    workflow_id=current.workflow_id,
                    run_id=current.run_id,
                    browser_id=browser_id,
                    remark="定时任务运行心跳预扣",
                )
                logger.debug(
                    f"[Membership] 心跳预扣: run={current.run_id}, "
                    f"target={target_total}s, already={already_charged}s, delta={delta}s"
                )
            await session.commit()
