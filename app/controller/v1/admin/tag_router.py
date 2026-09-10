"""资源标签（rpa_tag）RPA 侧仅保留管理员治理接口（update / delete）。

创建 / 打标（attach / detach）/ 列表（list / list-by-target）/ 审核（review）
均已下放由 **be-message 全托管**（见 `docs/rpa-tag-be-message-biz-计划书.md`）：
be-message 经 RPC 回调 RPA 存储（`rpc_server.create_tag/attach_tag/detach_tag/list_tags`），
审核经 `review_resource` 回落本表。RPA 侧这里只保留管理员修正标签名 / 颜色的治理操作。
"""
from bili_common.deps.auth import AuthInfo
from bili_common.models.response import (
    StandardResponse,
    error_response,
    success_response,
)
from bili_common.models.response_code import ResponseCode
from fastapi import APIRouter, Depends
from loguru import logger
from sqlmodel import select

from app.models.database.admin.models import ResourceTag, ResourceTagRel
from app.models.system.rpa_admin import DeleteTagRequest, TagItemResp, UpdateTagRequest
from app.services.admin_audit import log_admin_action
from app.utils.depends.admin_depends import require_admin
from app.utils.depends.session_manager import DatabaseSessionManager

router = APIRouter()  # tag 由 admin/__init__.py 聚合父路由统一提供


@router.post("/tag/update", response_model=StandardResponse[TagItemResp])
async def update_tag(
    request: UpdateTagRequest,
    auth: AuthInfo = Depends(require_admin),
):
    """更新标签（仅管理员；审核通过后仍可改名，将重新进入待审由 be-message 复核）"""
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
            rels = await session.exec(
                select(ResourceTagRel).where(ResourceTagRel.tag_id == request.id)
            )
            for rel in rels.all():
                await session.delete(rel)
            await session.delete(tag)
            await session.commit()
            await log_admin_action(auth.mid, "tag:delete", "tag", request.id)
            return success_response(data={"id": request.id}, msg="标签已删除")
    except Exception as e:
        logger.error(f"❌ 删除标签失败: {e}")
        return error_response(msg=f"删除失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR)


def _to_tag_item(t: ResourceTag) -> TagItemResp:
    return TagItemResp(
        id=t.id,
        name=t.name,
        color=t.color,
        created_by=t.created_by,
        audit_status=t.audit_status,
        pub_time=t.pub_time,
        created_at=t.created_at,
    )