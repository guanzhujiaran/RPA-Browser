from app.models.RPA_browser.plugin_model import (
    LogPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel,
    PageLimitPluginModel
)
from app.services.site_rpa_operation.plugins import (
    LogPlugin,
    RandomWaitPlugin,
    RetryPlugin,
    PageLimitPlugin,
    PluginTypeEnum
)


def get_plugin_model_class(plugin_type: PluginTypeEnum):
    """根据插件类型获取对应的SQLModel类"""
    model_map = {
        PluginTypeEnum.LOG: LogPluginModel,
        PluginTypeEnum.PAGE_LIMIT: PageLimitPluginModel,
        PluginTypeEnum.RANDOM_WAIT: RandomWaitPluginModel,
        PluginTypeEnum.RETRY: RetryPluginModel,
    }
    return model_map.get(plugin_type)


def get_plugin_class(plugin_type: PluginTypeEnum):
    """根据插件类型获取对应的插件类"""
    plugin_map = {
        PluginTypeEnum.LOG: LogPlugin,
        PluginTypeEnum.PAGE_LIMIT: PageLimitPlugin,
        PluginTypeEnum.RANDOM_WAIT: RandomWaitPlugin,
        PluginTypeEnum.RETRY: RetryPlugin,
    }
    return plugin_map.get(plugin_type)