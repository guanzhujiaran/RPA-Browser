"""
Plugin 模块 - 插件请求/响应模型

定义插件相关的API请求和响应模型。
"""

from typing import Union
from pydantic import BaseModel, Field, field_validator
from sqlmodel import SQLModel, Field as SQLField

from app.models.core.browser.fingerprint import LogPluginLogLevelEnum
from app.models.core.plugin.models import (
    LogPluginModel,
    PageLimitPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel,
)


class PluginCreateRequest(BaseModel):
    plugin_type: str = Field(
        ..., description="插件类型: log, page_limit, random_wait, retry"
    )
    browser_info_id: str | int | None = Field(None, description="浏览器实例ID")
    
    @field_validator("browser_info_id", mode="before")
    @classmethod
    def validate_browser_info_id(cls, v):
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            if v == "":
                return None
            try:
                return int(v)
            except ValueError:
                raise ValueError("browser_info_id must be a valid integer or numeric string")
        return v

    is_enabled: bool = Field(True, description="是否启用")
    name: str = Field(..., max_length=100, description="插件名称")
    description: str = Field("", max_length=500, description="插件描述")

    # 日志插件特有字段
    log_level: LogPluginLogLevelEnum | None = Field(None, description="日志级别")

    # 页面限制插件特有字段
    max_pages: int | None = Field(None, description="最大页面数")

    # 随机等待插件特有字段
    min_wait: float | None = Field(None, description="最小等待时间")
    mid_wait: float | None = Field(None, description="中等等待时间")
    max_wait: float | None = Field(None, description="最大等待时间")
    long_wait_interval: int | None = Field(None, description="长等待间隔")
    mid_wait_interval: int | None = Field(None, description="中等等待间隔")
    base_long_wait_prob: float | None = Field(None, description="基础长等待概率")
    base_mid_wait_prob: float | None = Field(None, description="基础中等待概率")
    prob_increase_factor: float | None = Field(None, description="概率增长因子")

    # 重试插件特有字段
    retry_times: int | None = Field(None, description="重试次数")
    delay: float | None = Field(None, description="延迟时间")
    is_push_msg_on_error: bool | None = Field(None, description="错误时是否推送消息")


class PluginGetRequest(BaseModel):
    """获取特定插件配置的请求模型"""
    plugin_type: str = Field(..., description="插件类型: log, page_limit, random_wait, retry")
    browser_info_id: str | int | None = Field(None, description="浏览器实例ID")

    @field_validator("browser_info_id", mode="before")
    @classmethod
    def validate_browser_info_id(cls, v):
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            if v == "":
                return None
            try:
                return int(v)
            except ValueError:
                raise ValueError("browser_info_id must be a valid integer or numeric string")
        return v


class PluginListRequest(BaseModel):
    """获取插件配置列表的请求模型"""
    browser_info_id: str | int | None = Field(None, description="浏览器实例ID")

    @field_validator("browser_info_id", mode="before")
    @classmethod
    def validate_browser_info_id(cls, v):
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            if v == "":
                return None
            try:
                return int(v)
            except ValueError:
                raise ValueError("browser_info_id must be a valid integer or numeric string")
        return v


class PluginDeleteRequest(BaseModel):
    """删除插件配置的请求模型"""
    plugin_id: str = Field(..., description="插件ID")


class PluginUpdateRequest(BaseModel):
    plugin_type: str = Field(
        ..., description="插件类型: log, page_limit, random_wait, retry"
    )
    browser_info_id: str | int | None = Field(None, description="浏览器实例ID")

    @field_validator("browser_info_id", mode="before")
    @classmethod
    def validate_browser_info_id(cls, v):
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            if v == "":
                return None
            try:
                return int(v)
            except ValueError:
                raise ValueError("browser_info_id must be a valid integer or numeric string")
        return v

    is_enabled: bool | None = Field(None, description="是否启用")
    name: str | None = Field(None, max_length=100, description="插件名称")
    description: str | None = Field(None, max_length=500, description="插件描述")

    # 日志插件特有字段
    log_level: LogPluginLogLevelEnum | None = Field(None, description="日志级别")

    # 页面限制插件特有字段
    max_pages: int | None = Field(None, description="最大页面数")

    # 随机等待插件特有字段
    min_wait: float | None = Field(None, description="最小等待时间")
    mid_wait: float | None = Field(None, description="中等等待时间")
    max_wait: float | None = Field(None, description="最大等待时间")
    long_wait_interval: int | None = Field(None, description="长等待间隔")
    mid_wait_interval: int | None = Field(None, description="中等等待间隔")
    base_long_wait_prob: float | None = Field(None, description="基础长等待概率")
    base_mid_wait_prob: float | None = Field(None, description="基础中等待概率")
    prob_increase_factor: float | None = Field(None, description="概率增长因子")

    # 重试插件特有字段
    retry_times: int | None = Field(None, description="重试次数")
    delay: float | None = Field(None, description="延迟时间")
    is_push_msg_on_error: bool | None = Field(None, description="错误时是否推送消息")


class PluginResponse(SQLModel):
    """插件响应基类"""
    id: int
    mid: str
    browser_info_id: str | None = None
    is_enabled: bool
    name: str
    description: str
    plugin_type: str
    is_virtual: bool


class LogPluginResponse(PluginResponse):
    """日志插件响应模型"""
    config: LogPluginModel


class PageLimitPluginResponse(PluginResponse):
    """页面限制插件响应模型"""
    config: PageLimitPluginModel


class RandomWaitPluginResponse(PluginResponse):
    """随机等待插件响应模型"""
    config: RandomWaitPluginModel


class RetryPluginResponse(PluginResponse):
    """重试插件响应模型"""
    config: RetryPluginModel


class PluginDictResponse(SQLModel):
    """插件字典响应模型 - 返回所有插件配置，包括默认配置"""
    log: LogPluginResponse = SQLField(..., description="日志插件配置")
    page_limit: PageLimitPluginResponse = SQLField(..., description="页面限制插件配置")
    random_wait: RandomWaitPluginResponse = SQLField(..., description="随机等待插件配置")
    retry: RetryPluginResponse = SQLField(..., description="重试插件配置")


__all__ = [
    "PluginCreateRequest",
    "PluginGetRequest",
    "PluginListRequest",
    "PluginDeleteRequest",
    "PluginUpdateRequest",
    "PluginResponse",
    "LogPluginResponse",
    "PageLimitPluginResponse",
    "RandomWaitPluginResponse",
    "RetryPluginResponse",
    "PluginDictResponse",
]
