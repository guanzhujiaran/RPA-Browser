"""签到奖励配置服务 - 基于 JSON 文件存储（管理员可自行编辑，热加载生效）

设计依据：docs/浏览器使用时长与会员权益计划书.md §13

约定：
    - 配置文件：app/data/sign_in_rewards.json（docker volume 挂载持久化）；
    - 按文件 mtime 缓存，文件变更后自动重载，改完即生效、无需重启；
    - 文件缺失 / 非法时回落内置默认配置并写回文件，保证服务始终可用。
"""

from __future__ import annotations

import json
from pathlib import Path

import aiofiles
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from app.config import settings

# 配置文件路径（app/data/sign_in_rewards.json）
CONFIG_FILE = Path(__file__).parent.parent.parent / "data" / "sign_in_rewards.json"


class SignRewardTierConfig(BaseModel):
    """档位配置：累计天数门槛 + 发放时长 + 发放补登卡"""

    tier: str = Field(description="档位标识，如 7d / 14d / 28d")
    days: int = Field(gt=0, description="解锁所需累计签到天数")
    reward_seconds: int = Field(ge=0, description="发放时长（秒）")
    makeup_cards: int = Field(ge=0, description="发放补登卡数量")

    @field_validator("tier")
    @classmethod
    def _tier_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("tier 不能为空")
        return v.strip()


class SignRewardConfig(BaseModel):
    """签到奖励整体配置（不含用户数据）"""

    launch_date: str = Field(
        default="2026-06-17", description="玩法上线日期 YYYY-MM-DD"
    )
    timezone: str = Field(default="Asia/Shanghai", description="签到判定时区")
    daily_reward_seconds: int = Field(ge=0, description="每日签到固定奖励（秒）")
    makeup_card_max: int = Field(ge=0, description="补登卡持有上限")
    makeup_window_days: int = Field(ge=0, description="补签窗口天数；0 = 仅限当月")
    tiers: list[SignRewardTierConfig] = Field(
        default_factory=list, description="档位列表（按 days 升序）"
    )

    @field_validator("tiers")
    @classmethod
    def _tiers_sorted_unique(
        cls, v: list[SignRewardTierConfig]
    ) -> list[SignRewardTierConfig]:
        if not v:
            raise ValueError("tiers 不能为空")
        days = [t.days for t in v]
        if len(set(days)) != len(days):
            raise ValueError("tiers 的 days 不能重复")
        if days != sorted(days):
            raise ValueError("tiers 必须按 days 升序")
        return v

    def get_tier(self, tier: str) -> SignRewardTierConfig | None:
        """按标识取档位配置"""
        for item in self.tiers:
            if item.tier == tier:
                return item
        return None

    def next_tier(self, total_sign_days: int) -> SignRewardTierConfig | None:
        """下一个未达成档位（全部达成返回 None）"""
        for item in self.tiers:
            if total_sign_days < item.days:
                return item
        return None


def build_default_config() -> SignRewardConfig:
    """内置默认配置（文件缺失 / 非法时使用）"""
    return SignRewardConfig(
        launch_date="2026-06-17",
        timezone="Asia/Shanghai",
        daily_reward_seconds=60 * 60,
        makeup_card_max=4,
        makeup_window_days=0,
        tiers=[
            SignRewardTierConfig(
                tier="7d", days=7, reward_seconds=60 * 60, makeup_cards=1
            ),
            SignRewardTierConfig(
                tier="14d", days=14, reward_seconds=180 * 60, makeup_cards=2
            ),
            SignRewardTierConfig(
                tier="28d", days=28, reward_seconds=480 * 60, makeup_cards=3
            ),
        ],
    )


class SignRewardConfigService:
    """签到奖励配置读写（热加载）"""

    CONFIG_FILE = CONFIG_FILE

    # mtime -> 配置 缓存
    _cache: SignRewardConfig | None = None
    _cache_mtime: float | None = None

    @classmethod
    def cached_timezone(cls) -> str:
        """同步读取当前生效时区名（供 app/utils/time_util.py 判定自然日）

        已热加载过配置时返回文件中的时区；冷启动阶段返回全局配置
        ``settings.app_timezone``，避免为此发起异步文件 IO。
        """
        if cls._cache is not None:
            return cls._cache.timezone
        return settings.app_timezone

    @classmethod
    async def get_config(cls) -> SignRewardConfig:
        """读取配置（文件 mtime 变化时自动重载）"""
        path = cls.CONFIG_FILE
        if not path.exists():
            logger.info(f"[SignReward] 配置文件不存在，写入默认配置: {path}")
            await cls.save_config(build_default_config())
            return build_default_config()

        mtime = path.stat().st_mtime
        if cls._cache is not None and cls._cache_mtime == mtime:
            return cls._cache

        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                raw = await f.read()
            config = SignRewardConfig.model_validate(json.loads(raw))
        except Exception as exc:  # noqa: BLE001 - 配置坏了不能拖垮签到，降级到默认配置
            logger.error(f"[SignReward] 配置文件解析失败，回落默认配置: {exc}")
            config = build_default_config()

        cls._cache = config
        cls._cache_mtime = mtime
        logger.info(
            f"[SignReward] 配置已加载: daily={config.daily_reward_seconds}s "
            f"tiers={[t.tier for t in config.tiers]}"
        )
        return config

    @classmethod
    async def save_config(cls, config: SignRewardConfig) -> None:
        """覆盖保存配置（下次读取即生效）"""
        cls.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(cls.CONFIG_FILE, "w", encoding="utf-8") as f:
            await f.write(config.model_dump_json(indent=2, exclude_none=True))
        # 缓存置空，强制下次按新文件重载
        cls._cache = None
        cls._cache_mtime = None
        logger.info(f"[SignReward] 配置已保存: {cls.CONFIG_FILE}")
