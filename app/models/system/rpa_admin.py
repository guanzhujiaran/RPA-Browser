"""
System 模块 - RPA 管理相关请求/响应模型（非表模型）
"""
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, SQLModel

from bili_common.models import AdminStatusResponse
from app.models.base.base_sqlmodel import BasePaginationReq, BasePaginationResp
from app.models.database.admin.models import (
    RpaAdmin,
    ApprovalRequest,
    ResourceTag,
    Certification,
)


# ===================== 管理员角色管理 =====================

class RpaAdminItemResp(SQLModel):
    """管理员信息响应"""

    id: int
    mid: int
    role: str
    granted_by: int
    permissions: List[str] = Field(default_factory=list)
    note: str = ""
    created_at: Optional[datetime] = None


class GrantAdminRequest(SQLModel):
    """授予管理员请求（仅 root）"""

    mid: int = Field(description="目标用户 mid")
    note: str = Field(default="", description="备注")
    permissions: List[str] = Field(default_factory=lambda: ["*"], description="权限列表")


class RevokeAdminRequest(SQLModel):
    """撤销管理员请求（仅 root）"""

    mid: int = Field(description="目标用户 mid")


class AdminListRequest(BasePaginationReq):
    """管理员列表请求（仅 root）"""


class AdminListResponse(BasePaginationResp[RpaAdminItemResp]):
    """管理员列表响应"""


# (AdminStatusResponse 已迁移至 bili-common，统一由 RPA 与 message 服务共用)


# ===================== 审批 =====================

class SubmitApprovalRequest(SQLModel):
    """提交审批请求"""

    resource_type: str = Field(description="资源类型：action / workflow / plugin")
    resource_id: str = Field(description="资源 ID（字符串形式）")
    action: str = Field(description="申请操作：publish / execute 等")
    title: str = Field(default="", description="申请标题")
    description: str = Field(default="", description="申请说明")


class ApprovalItemResp(SQLModel):
    """审批单响应"""

    id: int
    submitter_mid: int
    resource_type: str
    resource_id: str
    action: str
    title: str
    description: str
    status: str
    reviewer_mid: Optional[int] = None
    review_note: str = ""
    created_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None


class ApprovalListRequest(BasePaginationReq):
    """审批列表请求

    管理员/root 可查看全部；普通用户仅能看到自己提交的（后端按身份过滤）。
    """

    status: Optional[str] = Field(default=None, description="按状态过滤：pending/approved/rejected")
    resource_type: Optional[str] = Field(default=None, description="按资源类型过滤")


class ApprovalListResponse(BasePaginationResp[ApprovalItemResp]):
    """审批列表响应"""


class ReviewApprovalRequest(SQLModel):
    """审核审批请求（仅管理员/root）"""

    approval_id: int = Field(description="审批单 ID")
    status: str = Field(description="审核结果：approved / rejected")
    review_note: str = Field(default="", description="审核意见")


# ===================== 标签管理 =====================

class CreateTagRequest(SQLModel):
    name: str = Field(description="标签名称")
    color: str = Field(default="#409EFF", description="标签颜色")


class UpdateTagRequest(SQLModel):
    id: int = Field(description="标签 ID")
    name: Optional[str] = Field(default=None, description="新名称")
    color: Optional[str] = Field(default=None, description="新颜色")


class DeleteTagRequest(SQLModel):
    id: int = Field(description="标签 ID")


class TagItemResp(SQLModel):
    id: int
    name: str
    color: str
    created_by: int
    created_at: Optional[datetime] = None


class TagListResponse(BasePaginationResp[TagItemResp]):
    pass


class AttachTagRequest(SQLModel):
    tag_id: int = Field(description="标签 ID")
    target_type: str = Field(description="目标资源类型")
    target_id: str = Field(description="目标资源 ID")


class DetachTagRequest(SQLModel):
    tag_id: int = Field(description="标签 ID")
    target_type: str = Field(description="目标资源类型")
    target_id: str = Field(description="目标资源 ID")


class ListTagByTargetRequest(SQLModel):
    target_type: str = Field(description="目标资源类型")
    target_id: str = Field(description="目标资源 ID")


# ===================== 官方认证 =====================

class CertifyRequest(SQLModel):
    target_type: str = Field(description="目标资源类型：action / workflow / plugin")
    target_id: str = Field(description="目标资源 ID")
    note: str = Field(default="", description="认证备注")


class CertificationItemResp(SQLModel):
    id: int
    target_type: str
    target_id: str
    certified_by: int
    note: str
    created_at: Optional[datetime] = None


class CertificationListRequest(BasePaginationReq):
    target_type: Optional[str] = Field(default=None, description="按资源类型过滤")
    target_id: Optional[str] = Field(default=None, description="按资源 ID 过滤")


class CertificationListResponse(BasePaginationResp[CertificationItemResp]):
    pass


__all__ = [
    "RpaAdminItemResp",
    "GrantAdminRequest",
    "RevokeAdminRequest",
    "AdminListRequest",
    "AdminListResponse",
    "AdminStatusResponse",
    "SubmitApprovalRequest",
    "ApprovalItemResp",
    "ApprovalListRequest",
    "ApprovalListResponse",
    "ReviewApprovalRequest",
    "CreateTagRequest",
    "UpdateTagRequest",
    "DeleteTagRequest",
    "TagItemResp",
    "TagListResponse",
    "AttachTagRequest",
    "DetachTagRequest",
    "ListTagByTargetRequest",
    "CertifyRequest",
    "CertificationItemResp",
    "CertificationListRequest",
    "CertificationListResponse",
]
