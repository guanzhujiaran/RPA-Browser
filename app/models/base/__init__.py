"""
Base 模块 - 基础模型定义

提供 SQLModel 基类、分页模型等基础设施。
"""

from app.models.base.base_sqlmodel import (
    BaseSQLModel,
    BasePaginationReq,
    BasePaginationResp,
)

__all__ = [
    "BaseSQLModel",
    "BasePaginationReq",
    "BasePaginationResp",
]
