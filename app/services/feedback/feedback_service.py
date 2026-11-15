"""用户报错 / 反馈服务：落库 + 弱依赖站外推送服主。

落库是主链路（提交即成功返回）；推送是弱依赖的**单通道**（外部推送：邮件 / Webhook 等，
由服主自己的 ``NotificationConfig`` 决定渠道），失败只告警、绝不向上抛、不阻塞提交
（见 docs/用户提交报错与实时推送服主计划书.md §5）。

**不发站内通知**：服主明确只需站外推送（be-message 的 ``publish_notify`` 不参与）。

反馈**允许未登录提交**：登录态记录 ``mid`` + 昵称快照 ``uname``，
游客仅记录可选 ``contact``（联系方式）。
"""

from __future__ import annotations

import json
import uuid

from bili_common.deps.auth import AuthInfo
from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.models.database.feedback.models import RpaErrorReport
from app.models.feedback.feedback_requests import (
    FeedbackContext,
    FeedbackSubmitRequest,
)
from app.services.RPA_browser.browser import BrowserService
from app.services.message import push_msg
from app.utils.depends.session_manager import DatabaseSessionManager

# 推送正文里错误详情的截断阈值（完整内容看库，通知不宜过长）
_NOTIFY_DETAIL_MAX_CHARS = 2000


def build_context(request: FeedbackSubmitRequest) -> FeedbackContext:
    """把请求里扁平的可选上下文字段合并进 ``FeedbackContext``。"""
    return FeedbackContext(
        source=request.source,
        action_id=request.action_id,
        workflow_id=request.workflow_id,
        run_id=request.run_id,
        browser_id=request.browser_id,
        error_message=request.error_message,
        error_detail=request.error_detail,
        page_url=request.page_url,
        extra=request.extra,
    )


def _report_notify_content(report: RpaErrorReport) -> str:
    """组装推送服主的通知正文。"""
    context = FeedbackContext()
    if report.context:
        try:
            context = FeedbackContext.model_validate_json(report.context)
        except Exception:  # noqa: BLE001 - 上下文是展示性数据，解析失败不致命
            context = FeedbackContext()

    if report.mid is None:
        submitter = "未登录用户"
    elif report.uname:
        submitter = f"{report.uname}（mid={report.mid}）"
    else:
        submitter = f"mid={report.mid}"
    lines = [
        f"分类：{report.category}",
        f"标题：{report.title}",
        f"提交者：{submitter}",
    ]
    if report.contact:
        lines.append(f"联系方式：{report.contact}")
    field_labels = (
        ("关联动作", context.action_id),
        ("关联工作流", context.workflow_id),
        ("关联运行", context.run_id),
        ("关联浏览器", context.browser_id),
        ("页面", context.page_url),
    )
    for label, value in field_labels:
        if value is not None:
            lines.append(f"{label}：{value}")
    if context.error_message:
        lines.append(f"错误摘要：{context.error_message}")
    if context.error_detail:
        detail = context.error_detail
        if len(detail) > _NOTIFY_DETAIL_MAX_CHARS:
            detail = detail[:_NOTIFY_DETAIL_MAX_CHARS] + "…（完整内容见库）"
        lines.append(f"错误详情：{detail}")
    lines.append(f"描述：{report.description}")
    return "\n".join(lines)


async def create_report(
    session: AsyncSession,
    auth_info: AuthInfo | None,
    request: FeedbackSubmitRequest,
) -> RpaErrorReport:
    """落库一条报错 / 反馈，返回 ``RpaErrorReport``。

    Args:
        session: 数据库会话
        auth_info: 登录态信息；游客提交为 ``None``（mid / uname 均为空）
        request: 反馈内容
    """
    report = RpaErrorReport(
        report_id=uuid.uuid4().hex,
        mid=auth_info.mid if auth_info else None,
        uname=(auth_info.uname or auth_info.user_name) if auth_info else None,
        contact=request.contact,
        category=request.category.value,
        title=request.title,
        description=request.description,
        context=json.dumps(
            build_context(request).model_dump(exclude_none=True), ensure_ascii=False
        ),
        status="pending",
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report


async def notify_owners(report: RpaErrorReport) -> bool:
    """弱依赖**站外**推送服主，返回是否至少发出一条（不发站内通知）。

    只走 ``push_msg.send_error``：渠道由服主自己的 ``NotificationConfig``
    （邮件 / Webhook 等）决定，未配置则跳过。任何失败只 ``logger.warning``，
    绝不向上抛、不影响提交。
    """
    owner_mids = settings.feedback_notify_mid_list
    if not owner_mids:
        logger.debug("[Feedback] feedback_notify_mids 为空，跳过推送（仅落库）")
        return False

    content = _report_notify_content(report)
    notified = False

    for owner_mid in owner_mids:
        try:
            async with DatabaseSessionManager.async_session() as notify_session:
                config = await BrowserService(owner_mid).get_notification_config(
                    notify_session
                )
            if config:
                await push_msg.send_error(
                    content,
                    subject="用户报错反馈",
                    conf=config,
                    mid=owner_mid,
                )
                notified = True
            else:
                logger.debug(f"[Feedback] 服主 {owner_mid} 未配置站外推送，跳过")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[Feedback] 站外推送异常: owner={owner_mid}, error={exc}")

    return notified


__all__ = ["build_context", "create_report", "notify_owners"]
