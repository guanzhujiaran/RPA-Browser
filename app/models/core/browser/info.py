"""
Core 模块 - 浏览器信息模型

定义用户浏览器信息、默认设置等数据库模型。
"""

from app.config import settings
from pydantic import computed_field
from sqlalchemy import BIGINT
from sqlmodel import Field, SQLModel

from app.models.core.browser.fingerprint import (
    BaseFingerprintBrowserInitParams,
    PlatformEnum,
    BrowserEnum,
)
from app.models.base.base_sqlmodel import BaseSQLModel
from snowflake import SnowflakeGenerator

# 初始化雪花ID生成器
snowflake_generator = SnowflakeGenerator(
    instance=settings.snowflake_id, epoch=1735689600000, seq=2
)


class UserBrowserUserId(BaseSQLModel):
    """用户浏览器令牌基础模型"""

    mid: int = Field(unique=False, index=True, sa_type=BIGINT)

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)


class UserBrowserInfoBase(UserBrowserUserId):
    """用户浏览器信息基础模型"""

    id: int = Field(
        sa_type=BIGINT,
        default_factory=lambda: next(snowflake_generator),
        primary_key=True,
    )

    @computed_field
    @property
    def id_str(self) -> str:
        return str(self.id)

class UserBrowserServerSideDefaultSetting(UserBrowserInfoBase):
    default_proxy_server:str | None = settings.default_proxy_server
    default_platform:PlatformEnum | None = PlatformEnum.windows
    default_browser:BrowserEnum | None = BrowserEnum.chrome
    default_lang:str | None = "zh-CN"
    default_timezone:str | None = "Asia/Shanghai"
    default_viewport_width:int | None = 1920
    default_viewport_height:int | None = 1080
    default_timeout:int | None = 30000

class UserBrowserDefaultSetting(UserBrowserServerSideDefaultSetting, table=True):
    """用户浏览器默认设置模型

    注意：原有的插件配置（重试、等待、页面限制等）已移除，
    现在统一通过“自定义动作”或“工作流”来实现。
    此处仅保留最基础的指纹和浏览器环境默认值。
    """
    default_proxy_server: str | None = Field(default=None)

    # === 指纹默认配置 ===
    default_platform: PlatformEnum | None = Field(
        default=PlatformEnum.windows,
        description="默认操作系统平台",
    )
    default_browser: BrowserEnum | None = Field(
        ...,
        description="默认浏览器类型",
    )
    default_lang: str | None = Field(
        ...,
        description="默认语言",
        max_length=20,
    )
    default_timezone: str | None = Field(
        ...,
        description="默认时区",
        max_length=50,
    )

    # === 浏览器默认配置 ===
    default_viewport_width: int | None = Field(
        ..., ge=800, le=3840, description="默认视口宽度"
    )
    default_viewport_height: int | None = Field(
        ..., ge=600, le=2160, description="默认视口高度"
    )
    default_timeout: int | None = Field(
        ..., ge=1000, description="默认超时时间(毫秒)"
    )

class UserBrowserInfoWithoutPlugin(
    UserBrowserInfoBase, BaseFingerprintBrowserInitParams
):
    """带插件配置的用户浏览器信息模型"""

    pass


class UserBrowserInfo(UserBrowserInfoWithoutPlugin, table=True):
    """用户浏览器信息数据库模型"""

    __tablename__ = "userbrowserinfo"

    custom_name: str | None = Field(
        None,
        nullable=True,
        description="用户自定义的浏览器名称",
    )


# ============ UserBrowserDefaultSetting 请求/响应模型 ============


class UserBrowserDefaultSettingRequest(SQLModel):
    """用户浏览器默认设置请求模型"""

    default_proxy_server: str | None = None

    # === 指纹默认配置 ===
    default_platform: PlatformEnum | None = None
    default_browser: BrowserEnum | None = None
    default_lang: str | None = None
    default_timezone: str | None = None

    # === 浏览器默认配置 ===
    default_viewport_width: int | None = None
    default_viewport_height: int | None = None
    default_timeout: int | None = None


class UserBrowserDefaultSettingResponse(SQLModel):
    """用户浏览器默认设置响应模型"""
    default_proxy_server: str | None = None

    # === 指纹默认配置 ===
    default_platform: PlatformEnum | None = None
    default_browser: BrowserEnum | None = None
    default_lang: str | None = None
    default_timezone: str | None = None

    # === 浏览器默认配置 ===
    default_viewport_width: int | None = None
    default_viewport_height: int | None = None
    default_timeout: int | None = None


__all__ = [
    "UserBrowserUserId",
    "UserBrowserInfoBase",
    "UserBrowserServerSideDefaultSetting",
    "UserBrowserDefaultSetting",
    "UserBrowserInfoWithoutPlugin",
    "UserBrowserInfo",
    "UserBrowserDefaultSettingRequest",
    "UserBrowserDefaultSettingResponse",
]
