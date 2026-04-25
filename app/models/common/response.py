"""
Response 模块 - 统一响应模型和辅助函数
"""

from enum import Enum
from typing import Any, Generic, TypeVar
from pydantic import BaseModel

DataT = TypeVar("DataT")


class StandardResponse(BaseModel, Generic[DataT]):
    """统一响应格式"""
    code: int = 0
    msg: str = "success"
    data: DataT | None = None


def success_response(data: Any = None, msg: str = "success") -> dict:
    """
    构建成功响应
    
    Args:
        data: 响应数据
        msg: 响应消息
        
    Returns:
        dict: 标准响应字典
    """
    return {
        "code": 0,
        "msg": msg,
        "data": data
    }


def error_response(code: int, msg: str, data: Any = None) -> dict:
    """
    构建错误响应
    
    Args:
        code: 错误码
        msg: 错误消息
        data: 错误数据
        
    Returns:
        dict: 标准错误响应字典
    """
    return {
        "code": code,
        "msg": msg,
        "data": data
    }


def custom_response(code: int, msg: str, data: Any = None) -> dict:
    """
    构建自定义响应
    
    Args:
        code: 响应码
        msg: 响应消息
        data: 响应数据
        
    Returns:
        dict: 标准响应字典
    """
    return {
        "code": code,
        "msg": msg,
        "data": data
    }


__all__ = [
    "StandardResponse",
    "success_response",
    "error_response",
    "custom_response",
    "DataT",
]
