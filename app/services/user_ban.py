"""用户封禁服务

职责：
- 封禁（永久 / 临时）、解封、查询封禁状态与历史记录。
- 临时封禁采用「惰性过期」：查询时若发现 expired_at 已过，直接把记录置为 expired，
  无需额外的定时任务。
- 提供带 TTL 的内存缓存，供请求中间件做低成本的封禁拦截。

当前生效范围：仅 RPA 服务（scope=rpa），不影响评论/私信服务。
"""
from datetime import datetime, timedelta
from time import monotonic

from loguru import logger
from sqlmodel import select

from app.models.database.admin.models import BanStatus, BanType, UserBan
from app.utils.depends.session_manager import DatabaseSessionManager

# mid -> (缓存写入时刻, 是否处于封禁中)
_ban_cache: dict[int, tuple[float, bool]] = {}
_BAN_CACHE_TTL_SECONDS = 30.0


def invalidate_ban_cache(mid: int | None = None) -> None:
    """清除封禁缓存（封禁/解封后调用），mid 为空时清空全部"""
    if mid is None:
        _ban_cache.clear()
    else:
        _ban_cache.pop(mid, None)


async def get_active_ban(mid: int) -> UserBan | None:
    """获取该用户当前生效中的封禁记录，无则返回 None

    若命中的是已过期的临时封禁，会顺带把状态落库为 expired。
    """
    now = datetime.now()
    async with DatabaseSessionManager.async_session() as session:
        result = await session.exec(
            select(UserBan)
            .where(UserBan.mid == mid, UserBan.status == BanStatus.ACTIVE.value)
            .order_by(UserBan.id.desc())  # type: ignore[union-attr]
        )
        bans = result.all()
        active: UserBan | None = None
        dirty = False
        for ban in bans:
            if ban.expired_at is not None and ban.expired_at <= now:
                ban.status = BanStatus.EXPIRED.value
                session.add(ban)
                dirty = True
                continue
            if active is None:
                active = ban
        if dirty:
            await session.commit()
        return active


async def is_user_banned(mid: int, use_cache: bool = True) -> bool:
    """判断用户当前是否处于封禁中（带 TTL 缓存，供中间件调用）"""
    if mid <= 0:
        return False
    if use_cache:
        cached = _ban_cache.get(mid)
        if cached is not None and monotonic() - cached[0] < _BAN_CACHE_TTL_SECONDS:
            return cached[1]
    banned = await get_active_ban(mid) is not None
    _ban_cache[mid] = (monotonic(), banned)
    return banned


def resolve_expired_at(
    ban_type: str,
    expired_at: datetime | None,
    duration_minutes: int | None,
) -> datetime | None:
    """根据封禁类型解析到期时间，永久封禁返回 None

    临时封禁必须能解析出一个未来时间，否则抛 ValueError。
    """
    if ban_type == BanType.PERMANENT.value:
        return None
    if expired_at is not None:
        if expired_at <= datetime.now():
            raise ValueError("临时封禁的到期时间必须晚于当前时间")
        return expired_at
    if duration_minutes is not None and duration_minutes > 0:
        return datetime.now() + timedelta(minutes=duration_minutes)
    raise ValueError("临时封禁必须提供 expired_at 或 duration_minutes")


async def ban_user(
    mid: int,
    operator_mid: int,
    ban_type: str = BanType.PERMANENT.value,
    expired_at: datetime | None = None,
    duration_minutes: int | None = None,
    reason: str = "",
    note: str = "",
) -> UserBan:
    """封禁用户；若已有生效中的封禁，则覆盖更新为最新一次的封禁参数"""
    if ban_type not in (BanType.PERMANENT.value, BanType.TEMPORARY.value):
        raise ValueError("封禁类型只能为 permanent 或 temporary")

    real_expired_at = resolve_expired_at(ban_type, expired_at, duration_minutes)
    now = datetime.now()

    existing = await get_active_ban(mid)
    async with DatabaseSessionManager.async_session() as session:
        if existing is not None and existing.id is not None:
            ban = await session.get(UserBan, existing.id)
        else:
            ban = None

        if ban is None:
            ban = UserBan(
                mid=mid,
                ban_type=ban_type,
                status=BanStatus.ACTIVE.value,
                reason=reason,
                banned_by=operator_mid,
                banned_at=now,
                expired_at=real_expired_at,
                note=note,
            )
        else:
            # 已在封禁中：更新为最新一次封禁（续期 / 改为永久）
            ban.ban_type = ban_type
            ban.status = BanStatus.ACTIVE.value
            ban.reason = reason or ban.reason
            ban.banned_by = operator_mid
            ban.banned_at = now
            ban.expired_at = real_expired_at
            ban.note = note or ban.note
            ban.lifted_by = None
            ban.lifted_at = None
            ban.lift_reason = ""

        session.add(ban)
        await session.commit()
        await session.refresh(ban)

    invalidate_ban_cache(mid)
    logger.info(
        f"🚫 用户封禁: mid={mid} type={ban_type} expired_at={real_expired_at} by={operator_mid}"
    )
    return ban


async def lift_ban(mid: int, operator_mid: int, reason: str = "") -> UserBan | None:
    """解封用户，返回被解封的记录；用户当前未被封禁时返回 None"""
    existing = await get_active_ban(mid)
    if existing is None or existing.id is None:
        return None

    async with DatabaseSessionManager.async_session() as session:
        ban = await session.get(UserBan, existing.id)
        if ban is None:
            return None
        ban.status = BanStatus.LIFTED.value
        ban.lifted_by = operator_mid
        ban.lifted_at = datetime.now()
        ban.lift_reason = reason
        session.add(ban)
        await session.commit()
        await session.refresh(ban)

    invalidate_ban_cache(mid)
    logger.info(f"✅ 用户解封: mid={mid} by={operator_mid}")
    return ban


__all__ = [
    "get_active_ban",
    "is_user_banned",
    "ban_user",
    "lift_ban",
    "resolve_expired_at",
    "invalidate_ban_cache",
]
