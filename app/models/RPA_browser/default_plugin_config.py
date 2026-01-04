from pydantic import BaseModel
from app.models.RPA_browser.plugin_model import (
    LogPluginLogLevelEnum
)
from app.services.site_rpa_operation.plugins import PluginTypeEnum

# 插件配置模型定义
class LogPluginConfig(BaseModel):
    """日志插件配置"""
    name: str = "默认日志插件"
    description: str = "用户级别的默认日志记录插件"
    is_enabled: bool = True
    log_level: LogPluginLogLevelEnum = LogPluginLogLevelEnum.INFO


class PageLimitPluginConfig(BaseModel):
    """页面限制插件配置"""
    name: str = "默认页面限制插件"
    description: str = "用户级别的页面数量限制插件"
    is_enabled: bool = True
    max_pages: int = 5


class RandomWaitPluginConfig(BaseModel):
    """随机等待插件配置"""
    name: str = "默认随机等待插件"
    description: str = "用户级别的操作间随机等待插件"
    is_enabled: bool = True
    min_wait: float = 0.0
    mid_wait: float = 10.0
    max_wait: float = 30.0
    long_wait_interval: int = 10
    mid_wait_interval: int = 5
    base_long_wait_prob: float = 0.05
    base_mid_wait_prob: float = 0.15
    prob_increase_factor: float = 0.02


class RetryPluginConfig(BaseModel):
    """重试插件配置"""
    name: str = "默认重试插件"
    description: str = "用户级别的操作失败重试插件"
    is_enabled: bool = True
    retry_times: int = 3
    delay: float = 30.0
    is_push_msg_on_error: bool = True


# 默认插件配置映射
DEFAULT_PLUGIN_CONFIGS = {
    PluginTypeEnum.LOG: LogPluginConfig(),
    PluginTypeEnum.PAGE_LIMIT: PageLimitPluginConfig(),
    PluginTypeEnum.RANDOM_WAIT: RandomWaitPluginConfig(),
    PluginTypeEnum.RETRY: RetryPluginConfig()
}


def get_default_plugin_config(plugin_type: PluginTypeEnum) -> BaseModel:
    """
    获取指定类型的默认插件配置
    
    Args:
        plugin_type: 插件类型
        
    Returns:
        BaseModel: 默认插件配置模型实例
    """
    return DEFAULT_PLUGIN_CONFIGS.get(plugin_type, LogPluginConfig())


def is_plugin_config_changed(plugin_type: PluginTypeEnum, current_config: dict) -> bool:
    """
    检查插件配置是否与默认配置不同
    
    Args:
        plugin_type: 插件类型
        current_config: 当前配置字典
        
    Returns:
        bool: 如果配置与默认值不同返回True，否则返回False
    """
    default_config = get_default_plugin_config(plugin_type)
    
    # 需要忽略的通用字段
    ignore_fields = {"mid", "browser_info_id", "plugin_type", "id", "created_at", "updated_at"}
    
    # 检查所有相关配置字段
    all_keys = set(default_config.model_dump().keys()) | set(current_config.keys())
    
    for key in all_keys:
        # 忽略通用字段
        if key in ignore_fields:
            continue
            
        default_value = default_config.model_dump().get(key)
        current_value = current_config.get(key)
        
        # 检查值是否不同
        if default_value != current_value:
            return True
            
    return False


def get_plugin_config_diff(plugin_type: PluginTypeEnum, current_config: dict) -> dict:
    """
    获取插件配置与默认配置的差异
    
    Args:
        plugin_type: 插件类型
        current_config: 当前配置字典
        
    Returns:
        dict: 包含差异字段的字典
    """
    default_config = get_default_plugin_config(plugin_type)
    
    # 需要忽略的通用字段
    ignore_fields = {"mid", "browser_info_id", "plugin_type", "id", "created_at", "updated_at"}
    
    diff = {}
    
    # 检查所有相关配置字段
    all_keys = set(default_config.model_dump().keys()) | set(current_config.keys())
    
    for key in all_keys:
        # 忽略通用字段
        if key in ignore_fields:
            continue
            
        default_value = default_config.model_dump().get(key)
        current_value = current_config.get(key)
        
        # 如果值不同，记录差异
        if default_value != current_value:
            diff[key] = {
                "default": default_value,
                "current": current_value
            }
            
    return diff