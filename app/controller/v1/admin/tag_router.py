"""资源标签管理 API

- 标签 CRUD：仅管理员/root 可写；任意登录用户可读
- 标签关联：仅管理员/root 可写；任意登录用户可读
"""
from loguru import logger
from fastapi import APIRouter, Depends

from bili_common.deps.auth import AuthInfo, get_auth_info_from_header
from bili_common.models.response_code import ResponseCode
from bili_common.models.response import StandardResponse, success_response, error_response
from app.models.router.router_tag import RouterTag
from app.models.base.base_sqlmodel import BasePaginationReq
from app.models.system.rpa_admin import (
    CreateTagRequest,
    UpdateTagRequest,
    DeleteTagRequest,
    TagItemResp,
    TagListResponse,
    AttachTagRequest,
    DetachTagRequest,
    ListTagByTargetRequest,
)
from app.utils.depends.admin_depends import require_admin
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.admin_audit import log_admin_action
from app.models.database.admin.models import ResourceTag, ResourceTagRel
from sqlmodel import select, func

router = APIRouter(tags=[RouterTag.admin_management])


@router.post("/tag/create", response_model=StandardResponse[TagItemResp])
async def create_tag(
    request: CreateTagRequest,
    auth: AuthInfo = Depends(require_admin),
):
    """创建标签（仅管理员）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(select(ResourceTag).where(ResourceTag.name == request.name))
            if existing.first() is not None:
                return error_response(msg="标签名称已存在", code=ResponseCode.CONFLICT)
            tag = ResourceTag(name=request.name, color=request.color, created_by=auth.mid)
            session.add(tag)
            await session.commit()
            await session.refresh(tag)
            await log_admin_action(auth.mid, "tag:create", "tag", tag.id, f"name={request.name}")
            return success_response(data=_to_tag_item(tag), msg="标签已创建")
    except Exception as e:
        logger.error(f"❌ 创建标签失败: {e}")
        return error_response(msg=f"创建失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/tag/update", response_model=StandardResponse[TagItemResp])
async def update_tag(
    request: UpdateTagRequest,
    auth: AuthInfo = Depends(require_admin),
):
    """更新标签（仅管理员）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(ResourceTag).where(ResourceTag.id == request.id))
            tag = result.first()
            if tag is None:
                return error_response(msg="标签不存在", code=ResponseCode.NOT_FOUND)
            if request.name is not None:
                tag.name = request.name
            if request.color is not None:
                tag.color = request.color
            await session.commit()
            await session.refresh(tag)
            await log_admin_action(auth.mid, "tag:update", "tag", request.id, f"name={request.name}, color={request.color}")
            return success_response(data=_to_tag_item(tag), msg="标签已更新")
    except Exception as e:
        logger.error(f"❌ 更新标签失败: {e}")
        return error_response(msg=f"更新失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/tag/delete", response_model=StandardResponse[dict])
async def delete_tag(
    request: DeleteTagRequest,
    auth: AuthInfo = Depends(require_admin),
):
    """删除标签（仅管理员），同时清理关联"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(select(ResourceTag).where(ResourceTag.id == request.id))
            tag = result.first()
            if tag is None:
                return error_response(msg="标签不存在", code=ResponseCode.NOT_FOUND)
            rels = await session.exec(select(ResourceTagRel).where(ResourceTagRel.tag_id == request.id))
            for rel in rels.all():
                await session.delete(rel)
            await session.delete(tag)
            await session.commit()
            await log_admin_action(auth.mid, "tag:delete", "tag", request.id)
            return success_response(data={"id": request.id}, msg="标签已删除")
    except Exception as e:
        logger.error(f"❌ 删除标签失败: {e}")
        return error_response(msg=f"删除失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/tag/list", response_model=StandardResponse[TagListResponse])
async def list_tags(
    request: BasePaginationReq,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """列出标签（任意登录用户可读）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ResourceTag)
                .order_by(ResourceTag.id)
                .offset((request.page - 1) * request.per_page)
                .limit(request.per_page)
            )
            tags = result.all()
            total = await session.exec(select(func.count()).select_from(ResourceTag))
            total_count = total.first() or 0
            return success_response(
                data=TagListResponse(
                    page=request.page,
                    per_page=request.per_page,
                    total=total_count,
                    items=[_to_tag_item(t) for t in tags],
                )
            )
    except Exception as e:
        logger.error(f"❌ 列出标签失败: {e}")
        return error_response(msg=f"查询失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/tag/attach", response_model=StandardResponse[dict])
async def attach_tag(
    request: AttachTagRequest,
    auth: AuthInfo = Depends(require_admin),
):
    """为资源关联标签（仅管理员）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            tag_result = await session.exec(select(ResourceTag).where(ResourceTag.id == request.tag_id))
            if tag_result.first() is None:
                return error_response(msg="标签不存在", code=ResponseCode.NOT_FOUND)
            existing = await session.exec(
                select(ResourceTagRel).where(
                    (ResourceTagRel.tag_id == request.tag_id)
                    & (ResourceTagRel.target_type == request.target_type)
                    & (ResourceTagRel.target_id == request.target_id)
                )
            )
            if existing.first() is not None:
                return success_response(data={"tag_id": request.tag_id}, msg="标签已关联")
            rel = ResourceTagRel(
                tag_id=request.tag_id,
                target_type=request.target_type,
                target_id=request.target_id,
                created_by=auth.mid,
            )
            session.add(rel)
            await session.commit()
            return success_response(data={"tag_id": request.tag_id}, msg="已关联标签")
    except Exception as e:
        logger.error(f"❌ 关联标签失败: {e}")
        return error_response(msg=f"关联失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/tag/detach", response_model=StandardResponse[dict])
async def detach_tag(
    request: DetachTagRequest,
    auth: AuthInfo = Depends(require_admin),
):
    """移除资源上的标签（仅管理员）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            existing = await session.exec(
                select(ResourceTagRel).where(
                    (ResourceTagRel.tag_id == request.tag_id)
                    & (ResourceTagRel.target_type == request.target_type)
                    & (ResourceTagRel.target_id == request.target_id)
                )
            )
            rel = existing.first()
            if rel is None:
                return error_response(msg="关联不存在", code=ResponseCode.NOT_FOUND)
            await session.delete(rel)
            await session.commit()
            return success_response(data={"tag_id": request.tag_id}, msg="已移除标签")
    except Exception as e:
        logger.error(f"❌ 移除标签失败: {e}")
        return error_response(msg=f"移除失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


@router.post("/tag/list-by-target", response_model=StandardResponse[list[TagItemResp]])
async def list_tags_by_target(
    request: ListTagByTargetRequest,
    auth: AuthInfo = Depends(get_auth_info_from_header),
):
    """查询某资源关联的标签（任意登录用户可读）"""
    try:
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ResourceTag)
                .join(ResourceTagRel, ResourceTagRel.tag_id == ResourceTag.id)
                .where(
                    (ResourceTagRel.target_type == request.target_type)
                    & (ResourceTagRel.target_id == request.target_id)
                )
            )
            tags = result.all()
            return success_response(data=[_to_tag_item(t) for t in tags])
    except Exception as e:
        logger.error(f"❌ 查询标签失败: {e}")
        return error_response(msg=f"查询失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


def _to_tag_item(t: ResourceTag) -> TagItemResp:
    return TagItemResp(
        id=t.id,
        name=t.name,
        color=t.color,
        created_by=t.created_by,
        created_at=t.created_at,
    )
