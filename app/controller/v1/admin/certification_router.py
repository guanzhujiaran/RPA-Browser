"""官方认证标注 API（仅管理员/root 可写，任意登录用户可读）"""
from loguru import logger
from fastapi import APIRouter, Depends

from bili_common.deps.auth import AuthInfo, get_auth_info_from_header
from bili_common.models.response_code import ResponseCode
from bili_common.models.response import StandardResponse, success_response, error_response
from app.models.router.router_tag import RouterTag
from app.models.base.base_sqlmodel import BasePaginationReq
from app.models.system.rpa_admin import (
    CertifyRequest,
    CertificationItemResp,
    CertificationListRequest,
    CertificationListResponse,
)
from app.utils.depends.admin_depends import require_admin
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.admin_audit import log_admin_action
from app.models.database.admin.models import Certification
from sqlmodel import select, func

router = APIRouter(tags=[RouterTag.admin_management])


@router.post("/certification/certify", response_model=StandardResponse[CertificationItemResp])
async def certify(
    request: CertifyRequest,
    auth: AuthInfo = Depends(require_admin),
):
    """标注资源为官方认证（仅管理员），已认证则更新"""
    try:
        logger.info(f"🏅 管理员({auth.mid}) 认证: {request.target_type}/{request.target_id}")
        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(Certification).where(
                    (Certification.target_type == request.target_type)
                    & (Certification.target_id == request.target_id)
                )
            )
            cert = existing.first()
            if cert is None:
                cert = Certification(
                    target_type=request.target_type,
                    target_id=request.target_id,
                    certified_by=auth.mid,
                    note=request.note,
                )
                session.add(cert)
            else:
                cert.certified_by = auth.mid
                cert.note = request.note
            await session.commit()
            await session.refresh(cert)
            await log_admin_action(auth.mid, "cert:certify", request.target_type, request.target_id, request.note)
            return success_response(data=_to_cert_item(cert), msg="已标注官方认证")
    except Exception as e:
        logger.error(f"❌ 认证失败: {e}")
        return error_response(msg=f"认证失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/certification/revoke", response_model=StandardResponse[dict])
async def revoke_certification(
    request: CertifyRequest,
    auth: AuthInfo = Depends(require_admin),
):
    """撤销官方认证（仅管理员）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(Certification).where(
                    (Certification.target_type == request.target_type)
                    & (Certification.target_id == request.target_id)
                )
            )
            cert = existing.first()
            if cert is None:
                return error_response(msg="该资源未认证", code=ResponseCode.NOT_FOUND)
            await session.delete(cert)
            await session.commit()
            await log_admin_action(auth.mid, "cert:revoke", request.target_type, request.target_id)
            return success_response(data={"target_id": request.target_id}, msg="已撤销官方认证")
    except Exception as e:
        logger.error(f"❌ 撤销认证失败: {e}")
        return error_response(msg=f"撤销失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/certification/list", response_model=StandardResponse[CertificationListResponse])
async def list_certifications(
    request: CertificationListRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """查询官方认证列表（任意登录用户可读）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            stmt = select(Certification)
            if request.target_type:
                stmt = stmt.where(Certification.target_type == request.target_type)
            if request.target_id:
                stmt = stmt.where(Certification.target_id == request.target_id)

            count_stmt = select(func.count()).select_from(Certification)
            if request.target_type:
                count_stmt = count_stmt.where(Certification.target_type == request.target_type)
            if request.target_id:
                count_stmt = count_stmt.where(Certification.target_id == request.target_id)
            total_count = (await session.exec(count_stmt)).first() or 0

            result = await session.exec(
                stmt.order_by(Certification.id.desc())
                .offset((request.page - 1) * request.per_page)
                .limit(request.per_page)
            )
            items = [_to_cert_item(c) for c in result.all()]
            return success_response(
                data=CertificationListResponse(
                    page=request.page, per_page=request.per_page, total=total_count, items=items
                )
            )
    except Exception as e:
        logger.error(f"❌ 查询认证失败: {e}")
        return error_response(msg=f"查询失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


def _to_cert_item(c: Certification) -> CertificationItemResp:
    return CertificationItemResp(
        id=c.id,
        target_type=c.target_type,
        target_id=c.target_id,
        certified_by=c.certified_by,
        note=c.note,
        created_at=c.created_at,
    )
