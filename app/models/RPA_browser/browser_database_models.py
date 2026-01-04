from app.config import settings
from pydantic import computed_field
from sqlalchemy import BIGINT
from sqlmodel import Field, Relationship

from app.models.RPA_browser.browser_fingerprint_models import (
    BaseFingerprintBrowserInitParams,
)
from app.models.base.base_sqlmodel import BaseSQLModel
from snowflake import SnowflakeGenerator

# 初始化雪花ID生成器，配置为生成更小的ID
# epoch 使用 2025-01-01 的时间戳，使生成的 ID 更小
snowflake_generator = SnowflakeGenerator(
    instance=settings.snowflake_id, epoch=1735689600000, seq=2
)


class UserBrowserToken(BaseSQLModel):
    """用户浏览器令牌基础模型"""

    mid: int = Field(
        unique=False, index=True, sa_type=BIGINT
    )  # 由外部系统传入，代表微服务主系统的用户ID

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)


class UserBrowserInfoBase(UserBrowserToken):
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


class UserBrowserDefaultSetting(UserBrowserToken, table=True):
    """用户浏览器默认设置模型"""

    __tablename__ = "userbrowserdefaultsetting"

    id: int | None = Field(default=None, primary_key=True, sa_type=BIGINT)
    default_proxy_server: str | None = Field(None)

    @computed_field
    @property
    def proxy_server(self) -> str | None:
        return self.default_proxy_server or settings.default_proxy_server or None


class UserBrowserInfoWithoutPlugin(UserBrowserInfoBase, BaseFingerprintBrowserInitParams):
    """带插件配置的用户浏览器信息模型"""

    pass


class UserBrowserInfo(UserBrowserInfoWithoutPlugin, table=True):
    """用户浏览器信息数据库模型"""

    __tablename__ = "userbrowserinfo"

    # 建立一对一关系，一个浏览器信息对应一个插件配置（每种类型一个）
    # 使用字符串类型注解避免循环导入
    log_plugin: "LogPluginModel" = Relationship(
        back_populates="log_browser_info", sa_relationship_kwargs={"lazy": "selectin"}
    )
    page_limit_plugin: "PageLimitPluginModel" = Relationship(
        back_populates="page_limit_browser_info",
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    random_wait_plugin: "RandomWaitPluginModel" = Relationship(
        back_populates="random_wait_browser_info",
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    retry_plugin: "RetryPluginModel" = Relationship(
        back_populates="retry_browser_info", sa_relationship_kwargs={"lazy": "selectin"}
    )
