"""
System 模块 - 用户封禁相关请求/响应模型（非表模型）
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.base.base_sqlmodel import BasePaginationReq, BasePaginationResp
from app.models.database.admin.models import BanType


class BanUserRequest(SQLModel):
    """封禁用户请求（root 或持有 user:ban 权限的管理员）"""

    mid: int = Field(description="被封禁用户 mid")
    ban_type: str = Field(
        default=BanType.PERMANENT.value,
        description="封禁类型：permanent（永久）/ temporary（临时）",
    )
    duration_minutes: Optional[int] = Field(
        default=None,
        description="临时封禁时长（分钟），与 expired_at 二选一，expired_at 优先",
    )
    expired_at: Optional[datetime] = Field(
        default=None, description="临时封禁到期时间，优先于 duration_minutes"
    )
    reason: str = Field(default="", description="封禁理由")
    note: str = Field(default="", description="备注")


class LiftBanRequest(SQLModel):
    """解封请求（root 或持有 user:ban 权限的管理员）"""

    mid: int = Field(description="被解封用户 mid")
    reason: str = Field(default="", description="解封理由")


class UserBanItemResp(SQLModel):
    """封禁记录响应"""

    id: int
    mid: int
    ban_type: str
    status: str
    scope: str = "rpa"
    reason: str = ""
    banned_by: int
    banned_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None
    lifted_by: Optional[int] = None
    lifted_at: Optional[datetime] = None
    lift_reason: str = ""
    note: str = ""


class BanListRequest(BasePaginationReq):
    """封禁记录列表请求"""

    mid: Optional[int] = Field(default=None, description="按用户 mid 过滤")
    status: Optional[str] = Field(
        default=None, description="按状态过滤：active / lifted / expired"
    )


class BanListResponse(BasePaginationResp[UserBanItemResp]):
    """封禁记录列表响应"""


class BanStatusRequest(SQLModel):
    """查询指定用户封禁状态请求"""

    mid: int = Field(description="目标用户 mid")


class BanStatusResponse(SQLModel):
    """用户封禁状态响应"""

    mid: int
    is_banned: bool = False
    ban_type: Optional[str] = None
    reason: str = ""
    expired_at: Optional[datetime] = None
    banned_at: Optional[datetime] = None
    ban_id: Optional[int] = None


__all__ = [
    "BanUserRequest",
    "LiftBanRequest",
    "UserBanItemResp",
    "BanListRequest",
    "BanListResponse",
    "BanStatusRequest",
    "BanStatusResponse",
]
