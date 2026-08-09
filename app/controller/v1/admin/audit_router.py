"""管理员操作审计日志查询 API（仅管理员/root）

提供审计日志的分页查询，供中台「操作审计」界面展示治理操作痕迹。
"""
from fastapi import APIRouter, Depends
from sqlmodel import SQLModel, Field, select, func

from bili_common.deps.auth import AuthInfo
from bili_common.models.response import StandardResponse, success_response
from app.models.router.router_tag import RouterTag
from app.models.base.base_sqlmodel import BasePaginationResp
from app.utils.depends.admin_depends import require_admin
from app.utils.depends.session_manager import DatabaseSessionManager
from app.models.database.admin.models import AdminAuditLog

router = APIRouter(tags=[RouterTag.admin_management])


class AuditListItemResponse(SQLModel):
    """审计日志列表项响应"""
    id: int
    admin_mid: int
    action: str
    target_type: str
    target_id: str
    detail: str
    created_at: str

    @classmethod
    def from_row(cls, row: AdminAuditLog) -> "AuditListItemResponse":
        return cls(
            id=row.id,
            admin_mid=row.admin_mid,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            detail=row.detail or "",
            created_at=row.created_at.isoformat() if row.created_at else "",
        )


@router.post("/audit/list", summary="获取操作审计列表")
async def list_audit(
    request: dict | None = None,
    auth: AuthInfo = Depends(require_admin),
) -> StandardResponse[BasePaginationResp[AuditListItemResponse]]:
    """获取管理员操作审计列表（仅管理员/root）

    Args:
        request: {
            "page": 页码（默认1）,
            "per_page": 每页数量（默认50）,
            "action": 操作类型过滤（可选，如 role:grant）,
            "target_type": 目标类型过滤（可选）,
            "admin_mid": 管理员 mid 过滤（可选）
        }
    """
    if request is None:
        request = {}

    page = request.get("page", 1)
    per_page = request.get("per_page", 50)
    action = request.get("action")
    target_type = request.get("target_type")
    admin_mid = request.get("admin_mid")
    skip = (page - 1) * per_page

    async with DatabaseSessionManager.async_session() as session:
        stmt = select(AdminAuditLog)
        count_stmt = select(func.count()).select_from(AdminAuditLog)
        if action:
            stmt = stmt.where(AdminAuditLog.action == action)
            count_stmt = count_stmt.where(AdminAuditLog.action == action)
        if target_type:
            stmt = stmt.where(AdminAuditLog.target_type == target_type)
            count_stmt = count_stmt.where(AdminAuditLog.target_type == target_type)
        if admin_mid:
            stmt = stmt.where(AdminAuditLog.admin_mid == admin_mid)
            count_stmt = count_stmt.where(AdminAuditLog.admin_mid == admin_mid)

        total = (await session.exec(count_stmt)).first() or 0
        rows = (
            await session.exec(
                stmt.order_by(AdminAuditLog.id.desc()).offset(skip).limit(per_page)
            )
        ).all()

    items = [AuditListItemResponse.from_row(r) for r in rows]
    return success_response(
        BasePaginationResp[AuditListItemResponse](
            page=page, per_page=per_page, total=total, items=items
        )
    )
