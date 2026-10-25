"""工作流运行通知（开始 / 成功 / 失败 / 取消）

从 ``workflow_runner`` 拆出。双通道投递：

1. **外部推送**（PushMe / PushDeer / webhook 等）：只读用户的**全局**
   ``NotificationConfig``（不再按浏览器覆盖），未配置渠道时静默跳过；发送时携带
   ``mid``，由 be-message 投递侧统一按用户「外部推送总开关（push_enabled /
   免打扰时段）」拦截，本服务不再自行判断。
2. **站内系统通知**（be-message ``msg_notify``）：无需任何用户配置，仅受用户
   自身「接收系统通知」开关约束。

两条通道相互独立且均为弱依赖，任一失败只告警，绝不抛出打断执行。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.models.database.workflow.models import UserWorkflow
from app.models.runtime.workflow_progress import render_loop_summaries
from app.services.RPA_browser.browser import BrowserService
from app.services.message import push_msg
from app.services.mq.rpc_notify import publish_notify
from app.utils.depends.session_manager import DatabaseSessionManager

# 站内通知跳转地址（RPA 工作流管理页；无精确到单次运行的深链）
_WORKFLOW_PAGE_URL = "/app/rpa-browser/workflows"


def _run_notify_content(
    workflow: UserWorkflow,
    browser_id: int | None,
    run_id: str,
    *,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    duration_ms: float | None = None,
) -> str:
    """组装工作流运行通知正文（开始/结束共用主体，结束附结果）"""
    lines = [
        f"工作流：{workflow.name}（{workflow.workflow_id}）",
        f"触发方式：{workflow.trigger_type}",
        f"目标浏览器：{browser_id}",
        f"运行记录：{run_id}",
    ]
    if summary is not None:
        lines.append(
            f"执行结果：成功 {summary.get('success', 0)} / "
            f"失败 {summary.get('failed', 0)}（共 {summary.get('total', 0)} 步）"
        )
    if duration_ms is not None:
        lines.append(f"耗时：{duration_ms / 1000:.1f} 秒")
    if error_message:
        lines.append(f"失败详情：{error_message}")
    # 运行告警：被 on_error=continue 容忍的失败（不改变结论，但可能已降级执行）
    tolerated = int(summary.get("tolerated", 0)) if summary else 0
    if tolerated > 0:
        lines.append(f"运行告警：{tolerated} 个步骤失败但已容错继续")
        # 循环维度：上面的「成功/失败/共 N 步」只数顶层步骤（循环整体算 1 步），
        # 看不见体内成败，补一行聚合（见 docs/通知动作与运行告警计划书.md §D4）
        loop_text = render_loop_summaries(
            (summary or {}).get("loop_summaries") or []
        )
        if loop_text:
            lines.append(f"循环统计：{loop_text}")
        for sample in (summary or {}).get("tolerated_samples") or []:
            lines.append(f"  - {sample}")
        lines.append("提示：这类失败不改变运行结论，但可能已降级执行，请检查相关配置。")
    return "\n".join(lines)


async def _notify_run(
    workflow: UserWorkflow,
    mid: int,
    browser_id: int | None,
    run_id: str,
    *,
    phase: str,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    duration_ms: float | None = None,
) -> bool:
    """工作流运行通知（开始 / 成功 / 失败三态），双通道投递。

    标题携带 ``run_id`` 后缀：站内通知在 be-message 侧按 ``(target_value, title)``
    幂等判重，带上运行唯一标识才能区分不同运行、不被误吞。

    Returns:
        True 表示**外部推送**已发出（用于标记 run 记录 notified；站内通知不计入）。
    """
    external_sent = False
    try:
        if phase == "start":
            prefix, subject = "工作流开始执行", push_msg.PushSubject.INFO
        elif phase == "success":
            prefix, subject = "工作流执行成功", push_msg.PushSubject.SUCCESS
        elif phase == "cancelled":
            prefix, subject = "工作流已停止", push_msg.PushSubject.INFO
        else:
            prefix, subject = "工作流执行失败", push_msg.PushSubject.FAILURE
        title = f"{prefix}: {workflow.name} (#{run_id[:8]})"
        content = _run_notify_content(
            workflow,
            browser_id,
            run_id,
            summary=summary,
            error_message=error_message,
            duration_ms=duration_ms,
        )

        # 通道一：外部推送（未配置渠道时静默跳过）
        # 只读全局 NotificationConfig（browser_id=None），不再按浏览器覆盖；
        # 携带 mid，由 be-message 投递侧按用户「外部推送总开关」统一拦截。
        try:
            async with DatabaseSessionManager.async_session() as session:
                config = await BrowserService(mid).get_notification_config(session)
            if config:
                await push_msg.send(
                    title=title,
                    content=content,
                    conf=config,
                    subject=subject,
                    mid=mid,
                )
                external_sent = True
            else:
                logger.info(
                    f"[WorkflowRunner] 未配置外部推送，跳过: run={run_id}"
                )
        except Exception as exc:  # noqa: BLE001 - 通知失败绝不影响业务
            logger.warning(
                f"[WorkflowRunner] 外部推送异常: run={run_id}, error={exc}"
            )

        # 通道二：站内系统通知（be-message，无需配置；弱依赖静默降级）
        try:
            await publish_notify(
                mid, title, content, jump_url=_WORKFLOW_PAGE_URL
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[WorkflowRunner] 站内通知异常: run={run_id}, error={exc}"
            )
    except Exception as exc:  # noqa: BLE001 - 兜底，通知链路绝不向上抛
        logger.warning(f"[WorkflowRunner] 发送运行通知异常: run={run_id}, error={exc}")

    return external_sent
