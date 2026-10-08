"""兑换码管理 API（仅管理员/root）

提供兑换码的批量生成与查询，供运营发放时长卡 / 月卡。
用户侧核销走 /browser/membership/redeem（见 membership_router.py）。
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlmodel import SQLModel, Field

from bili_common.deps.auth import AuthInfo
from bili_common.models.response import StandardResponse, success_response
from app.models.base.base_sqlmodel import BasePaginationResp
from app.models.common.exceptions.base_exception import (
    InvalidMembershipParamException,
    InvalidSignRewardConfigException,
    InvalidSignRewardTierException,
)
from app.services.admin_audit import log_admin_action
from app.utils.depends.admin_depends import require_admin
from app.utils.depends.session_manager import DatabaseSessionManager
from app.models.database.membership.models import (
    RedeemCodeTypeEnum,
    RedemptionCode,
    SignRewardTierEnum,
)
from app.services.membership.membership_service import MembershipService
from app.services.membership.sign_reward_config_service import (
    SignRewardConfig,
    SignRewardConfigService,
    SignRewardTierConfig,
)

router = APIRouter()  # tag 由 admin/__init__.py 聚合父路由统一提供，避免 tags 重复


# ============ 参数转换（str -> 强类型，非法输入一律转业务异常而非 500） ============


def parse_code_type(raw: str) -> RedeemCodeTypeEnum:
    """解析码类型"""
    try:
        return RedeemCodeTypeEnum(raw)
    except ValueError as exc:
        raise InvalidMembershipParamException(
            f"码类型非法: {raw}（需 duration / month_card）"
        ) from exc


def parse_code_type_or_none(raw: str | None) -> RedeemCodeTypeEnum | None:
    """解析码类型（空值表示不过滤）"""
    return parse_code_type(raw) if raw else None


def to_int(
    raw: str, field: str, *, minimum: int = 0, maximum: int | None = None
) -> int:
    """把 str 形态的数值参数转 int 并做区间校验"""
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidMembershipParamException(f"{field} 必须是整数: {raw}") from exc
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}~{maximum}" if maximum is not None else f">= {minimum}"
        raise InvalidMembershipParamException(f"{field} 需满足 {bound}，当前 {value}")
    return value


def parse_expire_at(raw: str | None) -> datetime | None:
    """解析兑换码有效期 YYYY-MM-DD HH:MM:SS"""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise InvalidMembershipParamException(
            f"有效期 expire_at 需为 YYYY-MM-DD HH:MM:SS: {raw}"
        ) from exc


# ============ 请求 / 响应模型 ============


class GenerateCodesRequest(SQLModel):
    """批量生成兑换码请求（数值/日期均 str 传输）"""

    code_type: str = Field(description="码类型: duration / month_card")
    count: str = Field(description="生成数量（1~500）")
    max_uses: str = Field(default="1", description="每码最大兑换次数")
    duration_seconds: str = Field(
        default="0", description="时长卡面值（秒）；DURATION 必填"
    )
    card_days: str = Field(default="0", description="月卡天数；MONTH_CARD 必填")
    expire_at: str | None = Field(
        default=None, description="兑换码有效期 YYYY-MM-DD HH:MM:SS（可选）"
    )
    batch_no: str = Field(default="", description="批次号（可选，默认随机）")
    remark: str = Field(default="", description="备注")


class RedemptionCodeItem(SQLModel):
    """兑换码条目"""

    code: str = Field(description="兑换码")
    code_type: str = Field(description="码类型")
    duration_seconds: str = Field(description="时长卡面值（秒）")
    card_days: str = Field(description="月卡天数")
    max_uses: str = Field(description="最大兑换次数")
    used_count: str = Field(description="已兑换次数")
    is_enabled: bool = Field(description="是否启用")
    expire_at: str | None = Field(description="有效期")
    batch_no: str = Field(description="批次号")
    remark: str = Field(description="备注")
    created_at: str = Field(description="创建时间")

    @classmethod
    def from_row(cls, row: RedemptionCode) -> "RedemptionCodeItem":
        return cls(
            code=row.code,
            code_type=row.code_type.value
            if isinstance(row.code_type, RedeemCodeTypeEnum)
            else str(row.code_type),
            duration_seconds=str(row.duration_seconds),
            card_days=str(row.card_days),
            max_uses=str(row.max_uses),
            used_count=str(row.used_count),
            is_enabled=row.is_enabled,
            expire_at=row.expire_at.isoformat(sep=" ", timespec="seconds")
            if row.expire_at
            else None,
            batch_no=row.batch_no or "",
            remark=row.remark or "",
            created_at=row.created_at.isoformat(sep=" ", timespec="seconds"),
        )


class GenerateCodesResponse(SQLModel):
    """生成结果响应"""

    batch_no: str = Field(description="批次号")
    codes: list[str] = Field(description="生成的兑换码列表")


# ============ API ============


@router.post("/membership/codes/generate", summary="批量生成兑换码")
async def generate_membership_codes(
    req: GenerateCodesRequest,
    auth: AuthInfo = Depends(require_admin),
) -> StandardResponse[GenerateCodesResponse]:
    """批量生成时长卡 / 月卡兑换码（仅管理员/root，写审计日志）"""
    code_type = parse_code_type(req.code_type)
    count = to_int(
        req.count,
        "生成数量 count",
        minimum=1,
        maximum=MembershipService._MAX_CODE_BATCH,
    )
    max_uses = to_int(
        req.max_uses,
        "最大兑换次数 max_uses",
        minimum=1,
        maximum=MembershipService._MAX_CODE_USES,
    )
    duration_seconds = to_int(req.duration_seconds, "时长卡面值 duration_seconds")
    card_days = to_int(req.card_days, "月卡天数 card_days")
    expire_at = parse_expire_at(req.expire_at)

    async with DatabaseSessionManager.async_session() as session:
        created = await MembershipService.generate_codes(
            session,
            code_type=code_type,
            count=count,
            max_uses=max_uses,
            duration_seconds=duration_seconds,
            card_days=card_days,
            expire_at=expire_at,
            batch_no=req.batch_no,
            remark=req.remark,
        )
        # 注意：必须在 commit 前取出标量值（expire_on_commit=True，commit 后 ORM 属性过期）
        batch_no = created[0].batch_no if created else ""
        codes = [m.code for m in created]
        await session.commit()

    # 审计日志走独立连接写入：写失败只告警，不影响「码已生成」这一既有事实
    await log_admin_action(
        auth.mid,
        "membership:generate_codes",
        target_type="redemption_code",
        target_id=batch_no,
        detail=(
            f"type={code_type.value}, count={len(created)}, "
            f"duration={duration_seconds}s, card_days={card_days}, "
            f"max_uses={max_uses}"
        ),
    )

    return success_response(GenerateCodesResponse(batch_no=batch_no, codes=codes))


class ListCodesRequest(SQLModel):
    """兑换码分页查询请求"""

    page: str = Field(default="1", description="页码")
    per_page: str = Field(default="50", description="每页数量")
    batch_no: str | None = Field(default=None, description="批次号过滤")
    code_type: str | None = Field(default=None, description="码类型过滤")


@router.post("/membership/codes/list", summary="分页查询兑换码")
async def list_membership_codes(
    req: ListCodesRequest,
    auth: AuthInfo = Depends(require_admin),
) -> StandardResponse[BasePaginationResp[RedemptionCodeItem]]:
    """分页查询兑换码（仅管理员/root）"""
    code_type = parse_code_type_or_none(req.code_type)
    page = to_int(req.page, "页码 page", minimum=1)
    per_page = to_int(
        req.per_page,
        "每页数量 per_page",
        minimum=1,
        maximum=MembershipService._MAX_PER_PAGE,
    )
    async with DatabaseSessionManager.async_session() as session:
        result = await MembershipService.list_codes(
            session,
            batch_no=req.batch_no,
            code_type=code_type,
            page=page,
            per_page=per_page,
        )
        # 在 session 关闭前完成 ORM → DTO 转换（避免 DetachedInstanceError）
        response = BasePaginationResp[RedemptionCodeItem](
            page=result.page,
            per_page=result.per_page,
            total=result.total,
            items=[RedemptionCodeItem.from_row(i) for i in result.items],
        )
    return success_response(response)


# ============ 签到奖励配置（JSON 文件，管理员可自行编辑 / 接口保存） ============


class SignRewardTierPayload(SQLModel):
    """档位配置（str 传输，保存时转 int）"""

    tier: str = Field(description="档位标识: 7d / 14d / 28d")
    days: str = Field(description="解锁所需累计签到天数")
    reward_seconds: str = Field(description="发放时长（秒）")
    makeup_cards: str = Field(description="发放补登卡数量")


class SignRewardConfigPayload(SQLModel):
    """签到奖励配置整体覆盖保存请求（数值 str 传输）"""

    launch_date: str = Field(
        default="2026-06-17", description="玩法上线日期 YYYY-MM-DD"
    )
    timezone: str = Field(default="Asia/Shanghai", description="签到判定时区")
    daily_reward_seconds: str = Field(description="每日签到固定奖励（秒）")
    makeup_card_max: str = Field(description="补登卡持有上限")
    makeup_window_days: str = Field(default="0", description="补签窗口天数；0=仅限当月")
    tiers: list[SignRewardTierPayload] = Field(description="档位列表（按天数升序）")


class SignRewardTierResponse(SQLModel):
    """档位配置响应（数值以 str 传递）"""

    tier: str = Field(description="档位标识: 7d / 14d / 28d")
    days: str = Field(description="解锁所需累计签到天数")
    reward_seconds: str = Field(description="发放时长（秒）")
    makeup_cards: str = Field(description="发放补登卡数量")


class SignRewardConfigResponse(SQLModel):
    """签到奖励配置响应（数值以 str 传递，避免前端大整数精度问题）

    与内部模型 ``SignRewardConfig`` 解耦：内部字段调整不会直接变成 API breaking。
    """

    launch_date: str = Field(description="玩法上线日期 YYYY-MM-DD")
    timezone: str = Field(description="签到判定时区")
    daily_reward_seconds: str = Field(description="每日签到固定奖励（秒）")
    makeup_card_max: str = Field(description="补登卡持有上限")
    makeup_window_days: str = Field(description="补签窗口天数；0=仅限当月")
    tiers: list[SignRewardTierResponse] = Field(
        default_factory=list, description="档位列表（按天数升序）"
    )

    @classmethod
    def from_config(cls, config: SignRewardConfig) -> "SignRewardConfigResponse":
        return cls(
            launch_date=config.launch_date,
            timezone=config.timezone,
            daily_reward_seconds=str(config.daily_reward_seconds),
            makeup_card_max=str(config.makeup_card_max),
            makeup_window_days=str(config.makeup_window_days),
            tiers=[
                SignRewardTierResponse(
                    tier=item.tier,
                    days=str(item.days),
                    reward_seconds=str(item.reward_seconds),
                    makeup_cards=str(item.makeup_cards),
                )
                for item in config.tiers
            ],
        )


@router.post("/membership/sign_in/rewards/get", summary="读取签到奖励配置")
async def get_sign_reward_config(
    auth: AuthInfo = Depends(require_admin),
) -> StandardResponse[SignRewardConfigResponse]:
    """读取当前签到奖励配置（app/data/sign_in_rewards.json，仅管理员/root）"""
    config = await SignRewardConfigService.get_config()
    return success_response(SignRewardConfigResponse.from_config(config))


@router.post("/membership/sign_in/rewards/update", summary="保存签到奖励配置")
async def update_sign_reward_config(
    req: SignRewardConfigPayload,
    auth: AuthInfo = Depends(require_admin),
) -> StandardResponse[SignRewardConfigResponse]:
    """整体覆盖保存签到奖励配置（保存后即时生效，写审计日志）"""
    try:
        config = SignRewardConfig(
            launch_date=req.launch_date,
            timezone=req.timezone,
            daily_reward_seconds=to_int(
                req.daily_reward_seconds, "每日签到奖励 daily_reward_seconds"
            ),
            makeup_card_max=to_int(req.makeup_card_max, "补登卡上限 makeup_card_max"),
            makeup_window_days=to_int(
                req.makeup_window_days, "补签窗口 makeup_window_days"
            ),
            tiers=[
                SignRewardTierConfig(
                    tier=item.tier,
                    days=to_int(item.days, "档位天数 days", minimum=1),
                    reward_seconds=to_int(
                        item.reward_seconds, "档位奖励 reward_seconds"
                    ),
                    makeup_cards=to_int(item.makeup_cards, "档位补登卡 makeup_cards"),
                )
                for item in req.tiers
            ],
        )
        valid_tiers = {t.value for t in SignRewardTierEnum}
        unknown = [t.tier for t in config.tiers if t.tier not in valid_tiers]
        if unknown:
            raise InvalidSignRewardTierException(",".join(unknown))
    except InvalidSignRewardTierException:
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidSignRewardConfigException(str(exc)) from exc

    await SignRewardConfigService.save_config(config)
    # 审计日志独立写入：写失败只告警，不影响配置已保存这一事实
    await log_admin_action(
        auth.mid,
        "membership:update_sign_reward_config",
        target_type="sign_reward_config",
        target_id=config.launch_date,
        detail=(
            f"daily={config.daily_reward_seconds}s, max_cards={config.makeup_card_max}, "
            f"tiers={[(t.tier, t.days, t.reward_seconds, t.makeup_cards) for t in config.tiers]}"
        ),
    )
    return success_response(SignRewardConfigResponse.from_config(config))
