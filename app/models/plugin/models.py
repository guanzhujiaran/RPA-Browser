"""
Plugin 模块 - 插件数据库模型

定义插件相关的数据库表模型。
"""

from enum import StrEnum
from sqlmodel import Field, Enum, Column, Relationship
from sqlalchemy import BIGINT
from app.models.base.base_sqlmodel import BaseSQLModel
from app.models.core.browser_info import UserBrowserInfo
from app.models.core.fingerprint import LogPluginLogLevelEnum


class PluginBaseModel(BaseSQLModel):
    # 关联的mid
    mid: str = Field(index=True, sa_type=BIGINT)

    # 关联的browser_info id
    browser_info_id: str | None = Field(
        default=None, foreign_key="userbrowserinfo.id", index=True, sa_type=BIGINT
    )

    # 插件启用状态
    is_enabled: bool = Field(default=True)

    # 插件名称
    name: str = Field(..., max_length=100)

    # 插件描述
    description: str = Field(default="", max_length=500)


class PluginBaseModelWithoutMid(BaseSQLModel):
    # 用于API请求的基类，不包含mid字段，mid从header获取
    browser_info_id: str | None = Field(
        default=None, foreign_key="userbrowserinfo.id", index=True, sa_type=BIGINT
    )

    is_enabled: bool = Field(default=True)
    name: str = Field(..., max_length=100)
    description: str = Field(default="", max_length=500)


class LogPluginModel(PluginBaseModel, table=True):
    __tablename__ = "log_plugin"

    id: int | None = Field(
        default=None, primary_key=True, sa_column_kwargs={"autoincrement": True}
    )

    log_level: LogPluginLogLevelEnum = Field(
        default=LogPluginLogLevelEnum.INFO,
        sa_column=Column(name="log_level", type_=Enum(LogPluginLogLevelEnum)),
    )

    # 建立一对一反向关系
    log_browser_info: "UserBrowserInfo" = Relationship(back_populates="log_plugin")


class PageLimitPluginModel(PluginBaseModel, table=True):
    __tablename__ = "page_limit_plugin"

    id: int | None = Field(
        default=None, primary_key=True, sa_column_kwargs={"autoincrement": True}
    )

    max_pages: int = 5

    # 建立一对一反向关系
    page_limit_browser_info: "UserBrowserInfo" = Relationship(
        back_populates="page_limit_plugin"
    )


class RandomWaitPluginModel(PluginBaseModel, table=True):
    __tablename__ = "random_wait_plugin"

    id: int | None = Field(
        default=None, primary_key=True, sa_column_kwargs={"autoincrement": True}
    )

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

    # 建立一对一反向关系
    random_wait_browser_info: "UserBrowserInfo" = Relationship(
        back_populates="random_wait_plugin"
    )


class RetryPluginModel(PluginBaseModel, table=True):
    __tablename__ = "retry_plugin"

    id: int | None = Field(
        default=None, primary_key=True, sa_column_kwargs={"autoincrement": True}
    )

    retry_times: int = 3
    delay: float = 30.0
    is_push_msg_on_error: bool = True

    # 建立一对一反向关系
    retry_browser_info: "UserBrowserInfo" = Relationship(back_populates="retry_plugin")


# 用于API请求的模型类
class LogPluginCreate(PluginBaseModelWithoutMid):
    log_level: LogPluginLogLevelEnum = LogPluginLogLevelEnum.INFO


class PageLimitPluginCreate(PluginBaseModelWithoutMid):
    max_pages: int = 5


class RandomWaitPluginCreate(PluginBaseModelWithoutMid):
    min_wait: float = 1.0
    mid_wait: float = 10.0
    max_wait: float = 30.0
    long_wait_interval: int = 10
    mid_wait_interval: int = 5
    base_long_wait_prob: float = 0.05
    base_mid_wait_prob: float = 0.15
    prob_increase_factor: float = 0.02


class RetryPluginCreate(PluginBaseModelWithoutMid):
    retry_times: int = 3
    delay: float = 30.0
    is_push_msg_on_error: bool = True


__all__ = [
    "PluginBaseModel",
    "PluginBaseModelWithoutMid",
    "LogPluginModel",
    "PageLimitPluginModel",
    "RandomWaitPluginModel",
    "RetryPluginModel",
    "LogPluginCreate",
    "PageLimitPluginCreate",
    "RandomWaitPluginCreate",
    "RetryPluginCreate",
]
