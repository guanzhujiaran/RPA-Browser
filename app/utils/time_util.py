"""统一时间工具 - 业务自然日判定统一走配置时区（计划书 §13）

背景：
    容器 / 宿主机 TZ 常为 UTC，直接使用 ``datetime.now()`` 或 ``date.today()``
    会让「今天」与业务时区（默认 Asia/Shanghai）差 8 小时，导致签到落在前一日、
    ``stat_date`` 错位、跨实例自然日不一致等隐蔽问题。

约定：
    - 会员权益、使用统计、签到日历等所有**业务自然日判定**必须走本工具；
    - 返回一律是 **naive datetime / date**（不带 tzinfo），与库表中存储的 naive
      DATETIME 保持可比，避免 aware / naive 混用比较抛 ``TypeError``。
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from loguru import logger

from app.config import settings

#: 任何配置都不可用时的兜底时区（与签到默认配置保持一致）
FALLBACK_TIMEZONE = "Asia/Shanghai"


def _resolve_timezone() -> ZoneInfo:
    """按优先级解析业务时区：签到 JSON 配置 > 全局配置 > 内置兜底

    签到配置为运行时文件配置（管理员可改），优先级最高；文件尚未加载时
    （冷启动）退回全局配置 ``settings.app_timezone``，两者都不可用时兜底。
    """
    from app.services.membership.sign_reward_config_service import (
        SignRewardConfigService,
    )

    candidates = (
        SignRewardConfigService.cached_timezone(),
        settings.app_timezone,
        FALLBACK_TIMEZONE,
    )
    for name in candidates:
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - 非法时区名不能拖垮签到
            logger.warning(f"[TimeUtil] 非法时区配置，跳过该候选: {name}")
    return ZoneInfo(FALLBACK_TIMEZONE)


def local_timezone() -> ZoneInfo:
    """当前业务时区"""
    return _resolve_timezone()


def now_local() -> datetime:
    """业务时区下的当前时间（naive，可直接与库表 DATETIME 比较）"""
    return datetime.now(_resolve_timezone()).replace(tzinfo=None)


def today_local() -> date:
    """业务时区下的今天（自然日边界的唯一入口）"""
    return now_local().date()


__all__ = ["local_timezone", "now_local", "today_local", "FALLBACK_TIMEZONE"]
