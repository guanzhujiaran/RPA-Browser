from enum import StrEnum
import uuid
from typing import TYPE_CHECKING

from sqlmodel import Field, Enum, Column, Relationship
from sqlalchemy import BIGINT
from app.models.base.base_sqlmodel import BaseSQLModel

if TYPE_CHECKING:
    from app.models.RPA_browser.browser_info_model import UserBrowserInfo


class LogPluginLogLevelEnum(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class PluginBaseModel(BaseSQLModel):
    # 关联的browser_token
    browser_token: uuid.UUID = Field(index=True)

    # 关联的browser_info id (可为空，为空表示用户级别的默认插件)
    browser_info_id: int | None = Field(default=None, foreign_key="userbrowserinfo.id", index=True, sa_type=BIGINT)

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

    # 建立反向关系
    browser_info: "UserBrowserInfo" = Relationship()


class PageLimitPluginModel(PluginBaseModel, table=True):
    __tablename__ = "page_limit_plugin"

    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})

    max_pages: int = 5

    # 建立反向关系
    browser_info: "UserBrowserInfo" = Relationship()


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

    # 建立反向关系
    browser_info: "UserBrowserInfo" = Relationship()


class RetryPluginModel(PluginBaseModel, table=True):
    __tablename__ = "retry_plugin"

    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})

    retry_times: int = 3
    delay: float = 30.0
    is_push_msg_on_error: bool = True

    # 建立反向关系
    browser_info: "UserBrowserInfo" = Relationship()
