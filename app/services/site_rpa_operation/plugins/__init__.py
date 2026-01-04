from enum import StrEnum

from app.models.RPA_browser.plugin_model import (
    LogPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel,
    PageLimitPluginModel,
)
from .log_plugin import LogPlugin
from .page_limit_plugin import PageLimitPlugin
from .retry_plugin import RetryPlugin
from .random_wait_plugin import RandomWaitPlugin
from pydantic import computed_field


class PluginTypeEnum(StrEnum):
    """插件类型枚举"""

    LOG = "log"
    PAGE_LIMIT = "page_limit"
    RANDOM_WAIT = "random_wait"
    RETRY = "retry"

    @computed_field
    @property
    def str_2_model(self):
        return {
            PluginTypeEnum.LOG: LogPluginModel,
            PluginTypeEnum.PAGE_LIMIT: PageLimitPluginModel,
            PluginTypeEnum.RANDOM_WAIT: RandomWaitPluginModel,
            PluginTypeEnum.RETRY: RetryPluginModel,
        }[self]

    @computed_field
    @property
    def str_2_class(self):
        return {
            PluginTypeEnum.LOG: LogPlugin,
            PluginTypeEnum.PAGE_LIMIT: PageLimitPlugin,
            PluginTypeEnum.RANDOM_WAIT: RandomWaitPlugin,
            PluginTypeEnum.RETRY: RetryPlugin,
        }[self]


__all__ = [
    "RetryPlugin",
    "RandomWaitPlugin",
    "PageLimitPlugin",
    "LogPlugin",
    "PluginTypeEnum",
]
