"""
System 模块 - RPA 管理相关数据库模型

包含：
- RpaAdmin:          RPA 管理员（由 root 授予，持久化存储）
- ApprovalRequest:   RPA 操作审批单
- ResourceTag:       资源标签
- ResourceTagRel:    资源-标签关联
- Certification:     官方认证标注
- UserBan:           用户封禁记录（永久 / 临时）
"""
from datetime import datetime
from enum import StrEnum
from typing import List, Optional

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Field

from app.models.base.base_sqlmodel import BaseSQLModel


class RpaAdmin(BaseSQLModel, table=True):
    """RPA 管理员表

    root 用户可将指定 mid 设为 RPA 管理员，管理员拥有审批、标注官方认证、
    管理标签等特权。该表为管理员身份的持久化来源（请求头中的 role 仅区分
    root / normal，admin 身份必须查库）。
    """

    __tablename__ = "rpa_admin"

    id: int | None = Field(default=None, primary_key=True)
    mid: int = Field(index=True, unique=True, description="被设为管理员的用户 mid")
    role: str = Field(default="admin", description="管理员角色，目前固定为 admin")
    granted_by: int = Field(description="授予者 mid（root 的 mid）")
    permissions: List[str] = Field(
        default_factory=lambda: ["*"],
        sa_type=JSON,
        description="管理员权限列表，'*' 表示全部权限",
    )
    note: str = Field(default="", description="备注")


class ApprovalRequest(BaseSQLModel, table=True):
    """RPA 操作审批单

    普通用户提交某项 RPA 操作（如发布到社区、执行高风险操作）的审批申请，
    由管理员审核通过或驳回。
    """

    __tablename__ = "rpa_approval"

    id: Optional[int] = Field(default=None, primary_key=True)
    submitter_mid: int = Field(index=True, description="提交申请的用户 mid")
    resource_type: str = Field(description="资源类型：action / workflow / plugin")
    resource_id: str = Field(description="资源 ID（字符串形式，避免精度丢失）")
    action: str = Field(description="申请的操作类型，如 publish / execute")
    title: str = Field(default="", description="申请标题")
    description: str = Field(default="", description="申请说明")
    status: str = Field(
        default="pending", index=True, description="状态：pending / approved / rejected"
    )
    reviewer_mid: Optional[int] = Field(default=None, description="审核人 mid")
    review_note: str = Field(default="", description="审核意见")
    reviewed_at: Optional[datetime] = Field(default=None, description="审核时间")
    expires_at: Optional[datetime] = Field(
        default=None, description="审批有效期截止时间，为空表示永久有效"
    )


class ResourceTag(BaseSQLModel, table=True):
    """资源标签"""

    __tablename__ = "rpa_tag"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, description="标签名称（唯一）")
    color: str = Field(default="#409EFF", description="标签颜色（十六进制）")
    created_by: int = Field(description="创建者 mid")


class ResourceTagRel(BaseSQLModel, table=True):
    """资源-标签关联"""

    __tablename__ = "rpa_tag_rel"
    __table_args__ = (
        UniqueConstraint(
            "tag_id", "target_type", "target_id", name="uq_tag_rel_target"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tag_id: int = Field(index=True, description="标签 ID")
    target_type: str = Field(description="目标资源类型：action / workflow / plugin")
    target_id: str = Field(description="目标资源 ID")
    created_by: int = Field(description="关联创建者 mid")


class Certification(BaseSQLModel, table=True):
    """官方认证标注

    管理员可将社区中的资源标注为官方认证（verified）。
    """

    __tablename__ = "rpa_certification"

    id: Optional[int] = Field(default=None, primary_key=True)
    target_type: str = Field(index=True, description="目标资源类型：action / workflow / plugin")
    target_id: str = Field(unique=True, index=True, description="目标资源 ID（唯一）")
    certified_by: int = Field(description="认证者 mid")
    note: str = Field(default="", description="认证备注")


class AdminAuditLog(BaseSQLModel, table=True):
    """管理员操作审计日志

    记录所有管理员/root 的治理操作（授权、认证、标签、审批审核、举报审核等），
    用于事后追溯与责任界定。
    """

    __tablename__ = "admin_audit_log"

    id: int | None = Field(default=None, primary_key=True)
    admin_mid: int = Field(index=True, description="操作的管理员 mid")
    action: str = Field(
        description="操作类型，如 role:grant / cert:certify / tag:create / approval:review / report:review"
    )
    target_type: str = Field(default="", description="目标类型")
    target_id: str = Field(default="", description="目标 ID")
    detail: str = Field(default="", description="操作详情（JSON 文本）")
    created_at: datetime = Field(default_factory=datetime.now)


class BanType(StrEnum):
    """封禁时长类型"""

    PERMANENT = "permanent"  # 永久封禁
    TEMPORARY = "temporary"  # 临时封禁（到 expired_at 自动解封）


class BanStatus(StrEnum):
    """封禁记录状态"""

    ACTIVE = "active"  # 封禁生效中
    LIFTED = "lifted"  # 已被管理员手动解封
    EXPIRED = "expired"  # 临时封禁已到期自动失效


class UserBan(BaseSQLModel, table=True):
    """用户封禁记录表

    设计要点：
    - 一条记录 = 一次封禁，历史记录全量保留，便于追溯与申诉。
    - 同一 mid 同时最多只有一条 status=active 的记录（由服务层保证）。
    - 临时封禁通过 expired_at 判定；到期后由查询侧惰性置为 expired（无需定时任务）。
    - scope 预留生效范围，当前仅 rpa（RPA 服务侧拦截），后续可扩展 all / message。
    """

    __tablename__ = "rpa_user_ban"

    id: int | None = Field(default=None, primary_key=True)
    mid: int = Field(index=True, description="被封禁用户 mid")
    ban_type: str = Field(
        default=BanType.PERMANENT.value,
        description="封禁类型：permanent（永久）/ temporary（临时）",
    )
    status: str = Field(
        default=BanStatus.ACTIVE.value,
        index=True,
        description="状态：active / lifted / expired",
    )
    scope: str = Field(
        default="rpa", index=True, description="生效范围：rpa（当前）/ all（预留）"
    )
    reason: str = Field(default="", description="封禁理由")
    banned_by: int = Field(index=True, description="执行封禁的管理员 mid")
    banned_at: datetime = Field(default_factory=datetime.now, description="封禁开始时间")
    expired_at: Optional[datetime] = Field(
        default=None, index=True, description="封禁到期时间，为空表示永久封禁"
    )
    lifted_by: Optional[int] = Field(default=None, description="解封操作者 mid")
    lifted_at: Optional[datetime] = Field(default=None, description="解封时间")
    lift_reason: str = Field(default="", description="解封理由")
    note: str = Field(default="", description="备注")


__all__ = [
    "RpaAdmin",
    "ApprovalRequest",
    "ResourceTag",
    "ResourceTagRel",
    "Certification",
    "AdminAuditLog",
    "BanType",
    "BanStatus",
    "UserBan",
]
