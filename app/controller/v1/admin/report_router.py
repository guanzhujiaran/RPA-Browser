"""
举报管理路由 - 管理员功能

提供举报列表查看和举报审核功能。
审核决策：ignore（标记无效）/ warn（警告，通知待私信系统建成后接入）/ takedown（下架资源）。
"""
from bili_common.models.response import StandardResponse, success_response, error_response
from app.services.execution.crud_service import community_crud_svr
from app.models.database.workflow.models import (
    ResourceReport,
    ResourceType,
    ReportReason,
    ReportDecision,
)
from app.utils.depends.mid_depends import AuthInfo
from app.utils.depends.admin_depends import require_admin
from app.services.admin_audit import log_admin_action
from fastapi import APIRouter, Depends
from app.models.base.base_sqlmodel import BasePaginationResp
from sqlmodel import SQLModel, Field
from app.models.router.router_tag import RouterTag

router = APIRouter(tags=[RouterTag.admin_management])


def _get_resource_type_name(resource_type: ResourceType) -> str:
    """获取资源类型名称"""
    type_map = {
        ResourceType.CUSTOM_ACTION: "自定义操作",
        ResourceType.USER_WORKFLOW: "工作流",
        ResourceType.USER_PLUGIN: "插件",
    }
    return type_map.get(resource_type, "未知")


def _get_reason_name(reason: ReportReason) -> str:
    """获取举报理由名称"""
    reason_map = {
        ReportReason.SPAM: "垃圾信息",
        ReportReason.INAPPROPRIATE: "不当内容",
        ReportReason.VIOLATION: "违反规定",
        ReportReason.PLAGIARISM: "抄袭",
        ReportReason.OTHER: "其他",
    }
    return reason_map.get(reason, "未知")


class ReportListItemResponse(SQLModel):
    """举报列表项响应"""
    id: int
    mid: str = Field(max_length=255)
    resource_type: ResourceType
    resource_type_name: str
    resource_id: int
    reason: ReportReason
    reason_name: str
    description: str
    is_valid: bool
    decision: ReportDecision
    review_note: str
    reviewed_by_mid: str | None = Field(default=None, max_length=255)
    reviewed_at: str | None = None
    created_at: str

    @classmethod
    def from_report(cls, report: ResourceReport) -> "ReportListItemResponse":
        """从 ResourceReport ORM 模型构建响应对象"""
        assert report.id is not None, "report.id must not be None"
        return cls(
            id=report.id,
            mid=report.mid,
            resource_type=report.resource_type,
            resource_type_name=_get_resource_type_name(report.resource_type),
            resource_id=report.resource_id,
            reason=report.reason,
            reason_name=_get_reason_name(report.reason),
            description=report.description,
            is_valid=report.is_valid,
            decision=report.decision,
            review_note=report.review_note,
            reviewed_by_mid=report.reviewed_by_mid,
            reviewed_at=report.reviewed_at.isoformat() if report.reviewed_at else None,
            created_at=report.created_at.isoformat(),
        )


@router.post("/reports/list", summary="获取举报列表")
async def list_reports(
    request: dict | None = None,
    auth: AuthInfo = Depends(require_admin),
) -> StandardResponse[BasePaginationResp[ReportListItemResponse]]:
    """获取举报列表（仅管理员/root）

    Args:
        request: {
            "page": 页码（默认1）,
            "per_page": 每页数量（默认50）,
            "is_valid": 是否有效（None=全部, True=未处理, False=已处理）,
            "resource_type": 资源类型筛选（可选）
        }
    """
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
    total = await community_crud_svr.count_reports(
        is_valid=is_valid,
        resource_type=resource_type
    )

    reports = await community_crud_svr.list_reports(
        skip=skip,
        limit=per_page,
        is_valid=is_valid,
        resource_type=resource_type
    )

    items = [ReportListItemResponse.from_report(report) for report in reports]

    pagination = BasePaginationResp[ReportListItemResponse](
        page=page,
        per_page=per_page,
        total=total,
        items=items
    )

    return success_response(pagination)


class ReportReviewRequest(SQLModel):
    """举报审核请求"""
    report_id: int
    decision: str  # ignore / warn / takedown
    review_note: str = ""


@router.post("/reports/review", summary="审核举报")
async def review_report(
    request: ReportReviewRequest,
    auth: AuthInfo = Depends(require_admin),
) -> StandardResponse[dict]:
    """审核举报（仅管理员/root）

    decision:
        - ignore:   标记无效，资源保持不变
        - warn:     警告被举报人（通知待私信系统建成后接入）
        - takedown: 下架资源（设为非公开，从社区隐藏）
    """
    decision_map = {
        "ignore": ReportDecision.IGNORED,
        "warn": ReportDecision.WARNED,
        "takedown": ReportDecision.TAKEDOWN,
    }
    decision = decision_map.get(request.decision)
    if not decision:
        return error_response(400, "decision 必须为 ignore / warn / takedown")

    ok = await community_crud_svr.review_report(
        report_id=request.report_id,
        admin_mid=auth.mid,
        decision=decision,
        review_note=request.review_note,
    )
    if ok is None:
        return error_response(404, "举报记录不存在")
    if ok is False:
        return error_response(400, "该举报已被处理")
    await log_admin_action(auth.mid, "report:review", "report", request.report_id, f"decision={request.decision}, note={request.review_note}")
    return success_response({"message": "审核完成", "decision": request.decision})


@router.post("/reports/mark-invalid", summary="标记举报为无效")
async def mark_report_invalid(
    request: dict,
    auth: AuthInfo = Depends(require_admin),
) -> StandardResponse[dict]:
    """管理员标记举报为无效（等价于 ignore 决策）

    Args:
        request: {"report_id": <举报记录ID>, "review_note": <可选备注>}
    """
    report_id = request.get("report_id")
    if not report_id:
        return error_response(400, "缺少举报记录ID")
    success = await community_crud_svr.mark_report_invalid(
        report_id=report_id,
        admin_mid=auth.mid,
        review_note=request.get("review_note", "")
    )
    if success:
        await log_admin_action(auth.mid, "report:mark_invalid", "report", report_id)
        return success_response({"message": "举报已标记为无效"})
    else:
        return error_response(400, "操作失败，举报不存在或已被处理")
