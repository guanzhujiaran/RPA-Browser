"""
举报管理路由 - 管理员功能

提供举报列表查看和举报状态管理功能
"""
from loguru import logger
from typing import List
from app.models.response import StandardResponse, success_response, error_response
from app.services.execution.crud_service import community_crud
from app.models.database.workflow.models import ResourceReport, ResourceType, ReportReason
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from fastapi import Depends
from app.models.base.base_sqlmodel import BasePaginationResp
from .base import new_admin_router

router = new_admin_router()


class ReportListItemResponse:
    """举报列表项响应"""
    id: int
    mid: int
    resource_type: int
    resource_type_name: str
    resource_id: int
    reason: int
    reason_name: str
    description: str
    is_valid: bool
    reviewed_by_mid: int | None
    reviewed_at: str | None
    created_at: str

    def __init__(self, report: ResourceReport):
        self.id = report.id
        self.mid = report.mid
        self.resource_type = report.resource_type
        self.resource_type_name = self._get_resource_type_name(report.resource_type)
        self.resource_id = report.resource_id
        self.reason = report.reason
        self.reason_name = self._get_reason_name(report.reason)
        self.description = report.description
        self.is_valid = report.is_valid
        self.reviewed_by_mid = report.reviewed_by_mid
        self.reviewed_at = report.reviewed_at.isoformat() if report.reviewed_at else None
        self.created_at = report.created_at.isoformat()

    @staticmethod
    def _get_resource_type_name(resource_type: int) -> str:
        """获取资源类型名称"""
        type_map = {
            ResourceType.CUSTOM_ACTION.value: "自定义操作",
            ResourceType.USER_WORKFLOW.value: "工作流",
            ResourceType.USER_PLUGIN.value: "插件",
        }
        return type_map.get(resource_type, "未知")

    @staticmethod
    def _get_reason_name(reason: int) -> str:
        """获取举报理由名称"""
        reason_map = {
            ReportReason.SPAM.value: "垃圾信息",
            ReportReason.INAPPROPRIATE.value: "不当内容",
            ReportReason.VIOLATION.value: "违反规定",
            ReportReason.PLAGIARISM.value: "抄袭",
            ReportReason.OTHER.value: "其他",
        }
        return reason_map.get(reason, "未知")


@router.post("/reports/list", summary="获取举报列表")
async def list_reports(
    request: dict = None,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[BasePaginationResp[ReportListItemResponse]]:
    """获取举报列表（管理员用）
    
    Args:
        request: {
            "page": 页码（默认1）,
            "per_page": 每页数量（默认50）,
            "is_valid": 是否有效（None=全部, True=有效, False=无效）,
            "resource_type": 资源类型筛选（可选）
        }
    """
    # TODO: 添加管理员权限验证
    
    if request is None:
        request = {}
    
    page = request.get("page", 1)
    per_page = request.get("per_page", 50)
    is_valid = request.get("is_valid")
    resource_type = request.get("resource_type")
    
    skip = (page - 1) * per_page
    
    # 将 is_valid 字符串转换为布尔值
    if isinstance(is_valid, str):
        is_valid = is_valid.lower() == "true"
    
    # 获取总数
    total = await community_crud.count_reports(
        is_valid=is_valid,
        resource_type=resource_type
    )
    
    reports = await community_crud.list_reports(
        skip=skip,
        limit=per_page,
        is_valid=is_valid,
        resource_type=resource_type
    )
    
    items = [ReportListItemResponse(report) for report in reports]
    
    pagination = BasePaginationResp[ReportListItemResponse](
        page=page,
        per_page=per_page,
        total=total,
        items=items
    )
    
    return success_response(pagination)


@router.post("/reports/mark-invalid", summary="标记举报为无效")
async def mark_report_invalid(
    request: dict,
    auth: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[dict]:
    """管理员标记举报为无效
    
    Args:
        request: {"report_id": <举报记录ID>}
    """
    # TODO: 添加管理员权限验证
    
    report_id = request.get("report_id")
    if not report_id:
        return error_response(400, "缺少举报记录ID")
    
    success = await community_crud.mark_report_invalid(
        report_id=report_id,
        reviewer_mid=auth.mid
    )
    
    if success:
        return success_response({"message": "举报已标记为无效"})
    else:
        return error_response(400, "操作失败，举报不存在或已被标记")
