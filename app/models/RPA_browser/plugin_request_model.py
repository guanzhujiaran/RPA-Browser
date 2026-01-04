from typing import Optional, Union
from pydantic import BaseModel, Field
from sqlmodel import SQLModel, Field

from app.models.RPA_browser.plugin_model import (
    LogPluginLogLevelEnum,
    LogPluginModel,
    PageLimitPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel,
)


class PluginCreateRequest(BaseModel):
    plugin_type: str = Field(
        ..., description="插件类型: log, page_limit, random_wait, retry"
    )
    browser_info_id: Optional[Union[str, int]] = Field(None, description="浏览器实例ID")
    is_enabled: bool = Field(True, description="是否启用")
    name: str = Field(..., max_length=100, description="插件名称")
    description: str = Field("", max_length=500, description="插件描述")

    # 日志插件特有字段
    log_level: Optional[LogPluginLogLevelEnum] = Field(None, description="日志级别")

    # 页面限制插件特有字段
    max_pages: Optional[int] = Field(None, description="最大页面数")

    # 随机等待插件特有字段
    min_wait: Optional[float] = Field(None, description="最小等待时间")
    mid_wait: Optional[float] = Field(None, description="中等等待时间")
    max_wait: Optional[float] = Field(None, description="最大等待时间")
    long_wait_interval: Optional[int] = Field(None, description="长等待间隔")
    mid_wait_interval: Optional[int] = Field(None, description="中等等待间隔")
    base_long_wait_prob: Optional[float] = Field(None, description="基础长等待概率")
    base_mid_wait_prob: Optional[float] = Field(None, description="基础中等待概率")
    prob_increase_factor: Optional[float] = Field(None, description="概率增长因子")

    # 重试插件特有字段
    retry_times: Optional[int] = Field(None, description="重试次数")
    delay: Optional[float] = Field(None, description="延迟时间")
    is_push_msg_on_error: Optional[bool] = Field(None, description="错误时是否推送消息")


class PluginGetRequest(BaseModel):
    """获取特定插件配置的请求模型"""
    plugin_type: str = Field(..., description="插件类型: log, page_limit, random_wait, retry")
    browser_info_id: Optional[Union[str, int]] = Field(None, description="浏览器实例ID")


class PluginListRequest(BaseModel):
    """获取插件配置列表的请求模型"""
    browser_info_id: Optional[Union[str, int]] = Field(None, description="浏览器实例ID")


class PluginDeleteRequest(BaseModel):
    """删除插件配置的请求模型"""
    plugin_id: str = Field(..., description="插件ID")


class PluginUpdateRequest(BaseModel):
    plugin_type: str = Field(
        ..., description="插件类型: log, page_limit, random_wait, retry"
    )
    browser_info_id: Optional[Union[str, int]] = Field(None, description="浏览器实例ID")
    is_enabled: Optional[bool] = Field(None, description="是否启用")
    name: Optional[str] = Field(None, max_length=100, description="插件名称")
    description: Optional[str] = Field(None, max_length=500, description="插件描述")

    # 日志插件特有字段
    log_level: Optional[LogPluginLogLevelEnum] = Field(None, description="日志级别")

    # 页面限制插件特有字段
    max_pages: Optional[int] = Field(None, description="最大页面数")

    # 随机等待插件特有字段
    min_wait: Optional[float] = Field(None, description="最小等待时间")
    mid_wait: Optional[float] = Field(None, description="中等等待时间")
    max_wait: Optional[float] = Field(None, description="最大等待时间")
    long_wait_interval: Optional[int] = Field(None, description="长等待间隔")
    mid_wait_interval: Optional[int] = Field(None, description="中等等待间隔")
    base_long_wait_prob: Optional[float] = Field(None, description="基础长等待概率")
    base_mid_wait_prob: Optional[float] = Field(None, description="基础中等待概率")
    prob_increase_factor: Optional[float] = Field(None, description="概率增长因子")

    # 重试插件特有字段
    retry_times: Optional[int] = Field(None, description="重试次数")
    delay: Optional[float] = Field(None, description="延迟时间")
    is_push_msg_on_error: Optional[bool] = Field(None, description="错误时是否推送消息")


class PluginResponse(SQLModel):
    """插件响应基类"""

    # 基础插件属性
    id: int
    mid: str
    browser_info_id: Optional[str] = None
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

    log: LogPluginResponse = Field(..., description="日志插件配置")
    page_limit: PageLimitPluginResponse = Field(..., description="页面限制插件配置")
    random_wait: RandomWaitPluginResponse = Field(..., description="随机等待插件配置")
    retry: RetryPluginResponse = Field(..., description="重试插件配置")
