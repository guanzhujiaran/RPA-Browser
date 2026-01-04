from enum import StrEnum
from typing import TYPE_CHECKING

from sqlmodel import Field, Enum, Column, Relationship
from sqlalchemy import BIGINT
from app.models.base.base_sqlmodel import BaseSQLModel

if TYPE_CHECKING:
    from app.models.RPA_browser.browser_database_models import UserBrowserInfo


class LogPluginLogLevelEnum(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class PluginBaseModel(BaseSQLModel):
    # 关联的mid
    mid: str = Field(index=True, sa_type=BIGINT)

    # 关联的browser_info id (可为空，为空表示用户级别的默认插件)
    browser_info_id: str | None = Field(default=None, foreign_key="userbrowserinfo.id", index=True, sa_type=BIGINT)

    # 插件启用状态
    is_enabled: bool = Field(default=True)

    # 插件名称
    name: str = Field(..., max_length=100)

    # 插件描述
    description: str = Field(default="", max_length=500)


class PluginBaseModelWithoutMid(BaseSQLModel):
    # 用于API请求的基类，不包含mid字段，mid从header获取
    # 关联的browser_info id (可为空，为空表示用户级别的默认插件)
    browser_info_id: str | None = Field(default=None, foreign_key="userbrowserinfo.id", index=True, sa_type=BIGINT)

    # 插件启用状态
    is_enabled: bool = Field(default=True)

    # 插件名称
    name: str = Field(..., max_length=100)

    # 插件描述
    description: str = Field(default="", max_length=500)


class LogPluginModel(PluginBaseModel, table=True):
    __tablename__ = "log_plugin"

    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})

    log_level: LogPluginLogLevelEnum = Field(
        default=LogPluginLogLevelEnum.INFO,
        sa_column=Column(name='log_level', type_=Enum(LogPluginLogLevelEnum)),
    )

    # 建立一对一反向关系
    log_browser_info: "UserBrowserInfo" = Relationship(back_populates="log_plugin")


class PageLimitPluginModel(PluginBaseModel, table=True):
    __tablename__ = "page_limit_plugin"

    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})

    max_pages: int = 5

    # 建立一对一反向关系
    page_limit_browser_info: "UserBrowserInfo" = Relationship(back_populates="page_limit_plugin")


class RandomWaitPluginModel(PluginBaseModel, table=True):
    __tablename__ = "random_wait_plugin"

    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})

    min_wait: float = 1.0
    mid_wait: float = 10.0
    max_wait: float = 30.0

    # 等待策略配置
    long_wait_interval: int = 10  # 每10个操作强制长等待
    mid_wait_interval: int = 5  # 每5个操作强制中等待

    # 概率配置
    base_long_wait_prob: float = 0.05  # 基础长等待概率 5%
    base_mid_wait_prob: float = 0.15  # 基础中等待概率 15%

    # 概率增长因子
    prob_increase_factor: float = 0.02  # 每次操作概率增长2%

    # 建立一对一反向关系
    random_wait_browser_info: "UserBrowserInfo" = Relationship(back_populates="random_wait_plugin")


class RetryPluginModel(PluginBaseModel, table=True):
    __tablename__ = "retry_plugin"

    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})

    retry_times: int = 3
    delay: float = 30.0
    is_push_msg_on_error: bool = True

    # 建立一对一反向关系
    retry_browser_info: "UserBrowserInfo" = Relationship(back_populates="retry_plugin")


# 用于API请求的模型类，不包含mid字段
class LogPluginCreate(PluginBaseModelWithoutMid):
    log_level: LogPluginLogLevelEnum = LogPluginLogLevelEnum.INFO


class PageLimitPluginCreate(PluginBaseModelWithoutMid):
    max_pages: int = 5


class RandomWaitPluginCreate(PluginBaseModelWithoutMid):
    min_wait: float = 1.0
    mid_wait: float = 10.0
    max_wait: float = 30.0

    # 等待策略配置
    long_wait_interval: int = 10
    mid_wait_interval: int = 5

    # 概率配置
    base_long_wait_prob: float = 0.05
    base_mid_wait_prob: float = 0.15

    # 概率增长因子
    prob_increase_factor: float = 0.02


class RetryPluginCreate(PluginBaseModelWithoutMid):
    retry_times: int = 3
    delay: float = 30.0
    is_push_msg_on_error: bool = True
