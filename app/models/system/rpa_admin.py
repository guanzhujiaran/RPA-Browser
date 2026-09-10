"""
System 模块 - RPA 管理相关请求/响应模型（非表模型）

注：管理员身份与授权已迁移至 be-message 的 `msg_admin` 表，
本模块不再包含管理员角色管理模型。
"""
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, SQLModel

from bili_common.models import AdminStatusResponse
from app.models.base.base_sqlmodel import BasePaginationReq, BasePaginationResp
from app.models.database.admin.models import (
    ApprovalRequest,
    ResourceTag,
    Certification,
)


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
    only_mine: bool = Field(
        default=False, description="仅查看当前请求用户自己的申请（优先级高于管理员可看全部），用于「我的申请」")


class ApprovalListResponse(BasePaginationResp[ApprovalItemResp]):
    """审批列表响应"""


class ReviewApprovalRequest(SQLModel):
    """审核审批请求（仅管理员/root）"""

    approval_id: int = Field(description="审批单 ID")
    status: str = Field(description="审核结果：approved / rejected")
    review_note: str = Field(default="", description="审核意见")


class ApprovalCancelRequest(SQLModel):
    """撤回审批请求：仅可撤回自己提交且仍处于待审核状态的审批"""

    approval_id: int = Field(description="审批单 ID")


class ApprovalDeleteRequest(SQLModel):
    """删除审批请求：删除自己提交的审批记录"""

    approval_id: int = Field(description="审批单 ID")


class ResourceSearchRequest(SQLModel):
    """按名称搜索当前用户自己的资源，用于审批提交时的下拉选择"""

    resource_type: str = Field(description="资源类型：action / workflow / plugin")
    keyword: str = Field(default="", description="按资源名称模糊搜索（可为空，返回自己的全部资源）")
    per_page: int = Field(default=50, ge=1, le=200, description="最多返回条数")


class ResourceSearchItemResp(SQLModel):
    """资源搜索结果项"""

    resource_type: str = Field(description="资源类型：action / workflow / plugin")
    resource_id: str = Field(description="资源业务 ID（提交审批时作为 resource_id）")
    name: str = Field(default="", description="资源名称")
    created_at: Optional[datetime] = Field(default=None, description="资源创建时间")


class ResourceSearchResponse(SQLModel):
    """资源搜索结果"""

    items: List[ResourceSearchItemResp] = Field(default_factory=list)


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
    audit_status: str = "auditing"
    pub_time: Optional[datetime] = None
    created_at: Optional[datetime] = None


class ListTagRequest(BasePaginationReq):
    """标签列表请求（用户侧默认仅看 normal；显式传 audit_status 可过滤指定状态给管理端复用）"""

    audit_status: Optional[str] = Field(
        default=None, description="审核状态过滤：auditing / normal / rejected；不传默认 normal"
    )


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
    "ListTagRequest",
    "AttachTagRequest",
    "DetachTagRequest",
    "ListTagByTargetRequest",
    "CertifyRequest",
    "CertificationItemResp",
    "CertificationListRequest",
    "CertificationListResponse",
]
