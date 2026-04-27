"""
Plugin 模块 - 插件创建模型

定义插件相关的 API 请求模型（非数据库表模型）。
"""

from app.models.core.plugin.models import (
    PluginBaseModelWithoutMid,
    LogPluginLogLevelEnum,
)


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
    "LogPluginCreate",
    "PageLimitPluginCreate",
    "RandomWaitPluginCreate",
    "RetryPluginCreate",
]
