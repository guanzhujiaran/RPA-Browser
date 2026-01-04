"""
大整数处理工具类
用于将字符串格式的大整数转换为int类型
"""
from typing import Union


def str_to_int(value: str) -> int:
    """
    将字符串转换为整数，确保不丢失精度
    
    Args:
        value: 字符串格式的数字
        
    Returns:
        int: 转换后的整数
        
    Raises:
        ValueError: 当字符串不是有效的数字格式时抛出
    """
    if value is None:
        raise ValueError("Value cannot be None")
    
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid integer value: {value}")


def str_to_int_or_none(value: Union[str, None]) -> Union[int, None]:
    """
    将字符串转换为整数，如果值为None则返回None
    
    Args:
        value: 字符串格式的数字或None
        
    Returns:
        int | None: 转换后的整数或None
    """
    if value is None:
        return None
    return str_to_int(value)