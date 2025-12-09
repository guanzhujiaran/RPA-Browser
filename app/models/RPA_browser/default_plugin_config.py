from typing import Dict, Any
from app.models.RPA_browser.plugin_model import (
    LogPluginLogLevelEnum
)
from app.services.site_rpa_operation.plugins import PluginTypeEnum

# 默认插件配置
DEFAULT_PLUGIN_CONFIGS: Dict[PluginTypeEnum, Dict[str, Any]] = {
    PluginTypeEnum.LOG: {
        "name": "默认日志插件",
        "description": "用户级别的默认日志记录插件",
        "is_enabled": True,
        "log_level": LogPluginLogLevelEnum.INFO
    },
    PluginTypeEnum.PAGE_LIMIT: {
        "name": "默认页面限制插件",
        "description": "用户级别的页面数量限制插件",
        "is_enabled": True,
        "max_pages": 5
    },
    PluginTypeEnum.RANDOM_WAIT: {
        "name": "默认随机等待插件",
        "description": "用户级别的操作间随机等待插件",
        "is_enabled": True,
        "min_wait": 1.0,
        "mid_wait": 10.0,
        "max_wait": 30.0,
        "long_wait_interval": 10,
        "mid_wait_interval": 5,
        "base_long_wait_prob": 0.05,
        "base_mid_wait_prob": 0.15,
        "prob_increase_factor": 0.02
    },
    PluginTypeEnum.RETRY: {
        "name": "默认重试插件",
        "description": "用户级别的操作失败重试插件",
        "is_enabled": True,
        "retry_times": 3,
        "delay": 30.0,
        "is_push_msg_on_error": True
    }
}


def get_default_plugin_config(plugin_type: PluginTypeEnum) -> Dict[str, Any]:
    """
    获取指定类型的默认插件配置
    
    Args:
        plugin_type: 插件类型
        
    Returns:
        Dict[str, Any]: 默认插件配置
    """
    return DEFAULT_PLUGIN_CONFIGS.get(plugin_type, {})


def is_plugin_config_changed(plugin_type: PluginTypeEnum, current_config: Dict[str, Any]) -> bool:
    """
    检查插件配置是否与默认配置不同
    
    Args:
        plugin_type: 插件类型
        current_config: 当前配置
        
    Returns:
        bool: 如果配置与默认值不同返回True，否则返回False
    """
    default_config = get_default_plugin_config(plugin_type)
    
    # 检查所有非默认字段是否发生变化
    for key, value in current_config.items():
        # 忽略一些通用字段
        if key in ["browser_token", "browser_info_id", "plugin_type", "id"]:
            continue
            
        # 检查值是否与默认值不同
        if key in default_config and default_config[key] != value:
            return True
            
    return False