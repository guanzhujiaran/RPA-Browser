"""用户报错 / 反馈服务：落库 + 弱依赖实时推送服主。

落库是主链路（提交即成功返回）；推送是弱依赖双通道，任一失败只告警、
绝不向上抛、不阻塞提交（见 docs/用户提交报错与实时推送服主计划书.md §5）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from bili_common.models.notify import NotifyLevelEnum
from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.models.database.feedback.models import RpaErrorReport
from app.models.feedback.feedback_requests import FeedbackSubmitRequest
from app.services.RPA_browser.browser import BrowserService
from app.services.message import push_msg
from app.services.mq.rpc_notify import publish_notify
from app.utils.depends.session_manager import DatabaseSessionManager

# 站内通知跳转地址（前端反馈管理页；无精确到单条报告的深链）
_FEEDBACK_PAGE_URL = "/app/rpa-browser/feedback"

# 推送正文里错误详情的截断阈值（完整内容看库，通知不宜过长）
_NOTIFY_DETAIL_MAX_CHARS = 2000

#: 参与组装 context / 通知正文的可选上下文字段
_CONTEXT_FIELDS: tuple[str, ...] = (
    "source",
    "action_id",
    "workflow_id",
    "run_id",
    "browser_id",
    "error_message",
    "error_detail",
    "page_url",
)


def _build_context(request: FeedbackSubmitRequest) -> dict[str, Any]:
    """把请求里扁平的可选上下文字段合并进 ``context`` JSON。"""
    ctx: dict[str, Any] = {}
    for key in _CONTEXT_FIELDS:
        value = getattr(request, key, None)
        if value is not None:
            ctx[key] = value
    if request.extra:
        ctx["extra"] = request.extra
    return ctx


def _report_notify_content(report: RpaErrorReport) -> str:
    """组装推送服主的通知正文。"""
    ctx: dict[str, Any] = {}
    if report.context:
        try:
            ctx = json.loads(report.context)
        except Exception:  # noqa: BLE001 - 上下文是展示性数据，解析失败不致命
            ctx = {}

    lines = [
        f"分类：{report.category}",
        f"标题：{report.title}",
        f"提交者 mid：{report.mid}",
    ]
    field_labels = (
        ("关联动作", "action_id"),
        ("关联工作流", "workflow_id"),
        ("关联运行", "run_id"),
        ("关联浏览器", "browser_id"),
        ("页面", "page_url"),
    )
    for label, key in field_labels:
        if ctx.get(key) is not None:
            lines.append(f"{label}：{ctx[key]}")
    if ctx.get("error_message"):
        lines.append(f"错误摘要：{ctx['error_message']}")
    if ctx.get("error_detail"):
        detail = str(ctx["error_detail"])
        if len(detail) > _NOTIFY_DETAIL_MAX_CHARS:
            detail = detail[:_NOTIFY_DETAIL_MAX_CHARS] + "…（完整内容见库）"
        lines.append(f"错误详情：{detail}")
    lines.append(f"描述：{report.description}")
    return "\n".join(lines)


async def create_report(
    session: AsyncSession, mid: int, request: FeedbackSubmitRequest
) -> RpaErrorReport:
    """落库一条报错 / 反馈，返回 ``RpaErrorReport``。"""
    category = (
        request.category.value
        if hasattr(request.category, "value")
        else str(request.category)
    )
    report = RpaErrorReport(
        report_id=uuid.uuid4().hex,
        mid=mid,
        category=category,
        title=request.title,
        description=request.description,
        context=json.dumps(_build_context(request), ensure_ascii=False),
        status="pending",
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report


async def notify_owners(report: RpaErrorReport) -> bool:
    """弱依赖双通道推送服主，返回是否至少发出一条。

    - 站内系统通知（be-message ``publish_notify``）：无需配置，实时；
    - 外部推送（``push_msg.send_error``）：走服主自己的 ``NotificationConfig``
      （邮件 / Webhook 等），未配置则跳过，站内通知兜底。

    任一失败只 ``logger.warning``，绝不向上抛。
    """
    owner_mids = settings.feedback_notify_mid_list
    if not owner_mids:
        logger.debug("[Feedback] feedback_notify_mids 为空，跳过推送（仅落库）")
        return False

    title = f"用户报错反馈: {report.title} (#{report.report_id[:8]})"
    content = _report_notify_content(report)
    notified = False

    for owner_mid in owner_mids:
        # 通道一：站内系统通知（be-message）
        try:
            ok = await publish_notify(
                owner_mid,
                title,
                content,
                level=NotifyLevelEnum.WARNING,
                jump_url=_FEEDBACK_PAGE_URL,
            )
            notified = notified or ok
        except Exception as exc:  # noqa: BLE001 - 通知失败绝不影响业务
            logger.warning(f"[Feedback] 站内通知异常: owner={owner_mid}, error={exc}")

        # 通道二：外部推送（邮件 / Webhook，服主 NotificationConfig 决定渠道）
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
                logger.debug(
                    f"[Feedback] 服主 {owner_mid} 未配置外部推送，已用站内通知兜底"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[Feedback] 外部推送异常: owner={owner_mid}, error={exc}")

    return notified


__all__ = ["create_report", "notify_owners"]
