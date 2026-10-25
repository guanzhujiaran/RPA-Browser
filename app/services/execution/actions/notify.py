"""通知操作 —— 站内系统通知 + 外部推送（双通道）

设计见 ``docs/通知动作与运行告警计划书.md``：

- 本动作只负责「发通知」这一**通用机制**，什么时候该发由动作 JSON 的 ``if_else`` 决定；
- 两个通道与工作流运行通知同一套语义：
  1. 站内系统通知 ``publish_notify``（be-message ``msg_notify``，无需用户配置，
     只受用户「接收系统通知」开关约束）；
  2. 外部推送 ``push_msg.send``（读用户全局 ``NotificationConfig``，
     ``push_enabled`` / 免打扰由 be-message 投递侧统一拦截）；
- **弱依赖**：任一通道异常只记 warning，动作仍返回成功，绝不断工作流；
- **去重**：进程内按 ``(mid, dedupe_key)`` 记忆投递记录，默认按服务时区自然日去重，
  避免「循环里每轮都发一条」把用户刷屏（通知本身幂等，最坏多一条）。
"""

from __future__ import annotations

import time
from datetime import datetime

from bili_common.models.notify import NotifyLevelEnum
from loguru import logger

from app.config import settings
from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.action_params import NotifyParams
from app.models.execution.action_result_models import NotifyResult
from app.services.RPA_browser.browser import BrowserService
from app.services.execution.actions.base import (
    ActionResult,
    BaseAction,
    register_action_class,
)
from app.services.message import push_msg
from app.services.mq.rpc_notify import publish_notify
from app.utils.depends.session_manager import DatabaseSessionManager

#: 去重表：(mid, dedupe_key) -> 投递日期（服务时区自然日；非按天去重时为 ""）
_DEDUPE: dict[tuple[str, str], str] = {}

#: 通知级别 → 外部推送主题（[i] 信息 / [w] 警告 / [f] 失败）
_LEVEL_SUBJECT: dict[NotifyLevelEnum, str] = {
    NotifyLevelEnum.NORMAL: push_msg.PushSubject.INFO,
    NotifyLevelEnum.IMPORTANT: push_msg.PushSubject.WARNING,
    NotifyLevelEnum.URGENT: push_msg.PushSubject.FAILURE,
}


def _today() -> str:
    """服务时区（settings.app_timezone）的当前自然日，用于按天去重。"""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(settings.app_timezone)).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001 - 时区不可用时不阻塞通知
        return datetime.now().strftime("%Y-%m-%d")


def _dedupe_bucket(once_per_day: bool) -> str:
    """去重窗口标识：按天去重取日期，否则固定空串（进程内只发一次）。"""
    return _today() if once_per_day else ""


def reset_dedupe() -> None:
    """清空去重表（测试 / 手动重发用）。"""
    _DEDUPE.clear()


class NotifyAction(BaseAction[NotifyParams]):
    """发送通知（站内系统通知 + 外部推送）"""

    action_id: BuiltinActionType = BuiltinActionType.NOTIFY
    action_type: BuiltinActionType = BuiltinActionType.NOTIFY
    params: NotifyParams

    async def _execute(self) -> ActionResult[NotifyResult]:
        start_time = time.time()

        valid, error_msg, p = self.validate_params_with_model(self.params)
        if not valid or not p:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        mid = str(self.mid)
        title = (p.title or "").strip()
        if not title:
            return ActionResult(
                success=False,
                error="通知标题不能为空",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        key = (p.dedupe_key or title).strip()
        bucket = _dedupe_bucket(p.once_per_day)
        if (mid, key) in _DEDUPE and _DEDUPE[(mid, key)] == bucket:
            logger.info(
                f"[NotifyAction] 命中去重，跳过发送: mid={mid}, key={key}, "
                f"window={'按天' if p.once_per_day else '进程内'}"
            )
            return ActionResult(
                success=True,
                data=NotifyResult(deduped=True),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        inbox_sent = False
        push_sent = False

        # ── 通道一：外部推送（未配置渠道时静默跳过）──
        if p.channel in ("both", "push"):
            try:
                async with DatabaseSessionManager.async_session() as session:
                    config = await BrowserService(int(mid)).get_notification_config(session)
                if config:
                    await push_msg.send(
                        title=title,
                        content=p.content or "",
                        conf=config,
                        subject=_LEVEL_SUBJECT.get(
                            p.level, push_msg.PushSubject.INFO
                        ),
                        mid=int(mid),
                    )
                    push_sent = True
                else:
                    logger.info(f"[NotifyAction] 未配置外部推送，跳过: mid={mid}")
            except Exception as exc:  # noqa: BLE001 - 通知失败绝不影响业务
                logger.warning(f"[NotifyAction] 外部推送异常: mid={mid}, error={exc}")

        # ── 通道二：站内系统通知（无需配置；弱依赖静默降级）──
        if p.channel in ("both", "inbox"):
            try:
                inbox_sent = bool(
                    await publish_notify(
                        mid,
                        title,
                        p.content or "",
                        level=p.level,
                        jump_url=p.jump_url,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[NotifyAction] 站内通知异常: mid={mid}, error={exc}")

        # 至少一个通道成功才记去重；全失败时保留后续重试的机会
        if inbox_sent or push_sent:
            _DEDUPE[(mid, key)] = bucket

        logger.info(
            f"[NotifyAction] 通知已处理: mid={mid}, level={p.level.value}, "
            f"inbox={inbox_sent}, push={push_sent}, key={key}"
        )
        return ActionResult(
            success=True,
            data=NotifyResult(
                deduped=False, inbox_sent=inbox_sent, push_sent=push_sent
            ),
            execution_time=time.time() - start_time,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )


register_action_class(NotifyAction)
