"""
参数工具类 - 用于处理从headers获取mid后的参数组合
"""
import uuid
from typing import Any


def combine_params_with_mid(params: Any, mid: uuid.UUID) -> Any:
    """
    将参数对象与mid组合成一个新的参数对象
    
    Args:
        params: 原始参数对象
        mid: 从headers获取的mid
        
    Returns:
        新的参数对象，包含mid字段
    """
    # 创建一个动态类来包含mid和原始参数
    param_dict = params.model_dump() if hasattr(params, 'model_dump') else {}
    param_dict['mid'] = mid
    
    # 创建动态类实例
    return type("", (), param_dict)()