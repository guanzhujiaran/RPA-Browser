"""工作流运行状态变更事件（进程内总线）

第二步：把「状态变了之后要做的副作用」从执行主体里解耦出来
（见 ``docs/工作流运行状态机计划书.md`` §7）。

约定：
    - 事件只在**状态落库成功之后**发布，不参与事务，也不会回滚写入；
    - 订阅者失败只告警，绝不影响状态落库与运行流程；
    - 只在本进程分发：手动运行（HTTP 协程）与定时运行（APScheduler）共享同一
      事件循环；多实例部署时换成 DB outbox / Redis，订阅者代码不用改；
    - DB 仍是唯一真相：事件丢了只影响「派生态」（如工作流 last_run_status 刷新），
      不影响运行记录本身。

订阅者注册：``app/services/execution/run_event_handlers.py``，
服务启动时由 ``app/setup.py`` 统一注册。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from loguru import logger
from pydantic import Field
from sqlmodel import SQLModel

from app.models.database.workflow.models import WorkflowRunStatusEnum
from app.models.runtime.workflow_progress import LoopRunSummary


class WorkflowRunSummary(SQLModel):
    """运行结果摘要（终态事件附带）。

    为什么放在事件里：通知正文与计费结算需要「运行期上下文」（步骤计数、耗时、
    会话获取后的可计费时长），这些值只能在执行体里算出来。把它们做成事件契约，
    订阅者就不必再回头读执行期对象，也不用猜。

    非终态事件（创建 / 排队转运行 / 排队取消）没有这些值，``summary`` 为 None。
    """

    total: int = Field(default=0, description="步骤总数")
    success_count: int = Field(default=0, description="成功步骤数")
    failed_count: int = Field(default=0, description="失败步骤数")
    duration_ms: float = Field(default=0.0, description="运行总耗时（毫秒）")
    billable_seconds: float = Field(
        default=0.0, description="可计费时长（秒）：从拿到浏览器会话到运行结束"
    )
    error_message: str | None = Field(default=None, description="失败 / 取消原因")
    # ── 运行告警：被 on_error=continue 容忍的失败（不改变运行结论，但要告知用户）──
    tolerated_count: int = Field(
        default=0, description="被容错继续的失败步骤数（降级次数）"
    )
    tolerated_samples: list[str] = Field(
        default_factory=list, description="前几条容忍失败的错误摘要（去重、截断）"
    )
    # ── 循环聚合：顶层步骤计数看不见循环体内成败，熔断文案/通知需要该维度 ──
    #（见 docs/通知动作与运行告警计划书.md §D4）
    loop_summaries: list[LoopRunSummary] = Field(
        default_factory=list,
        description="已结束循环的聚合（每实例一条：轮次 + 体内动作成败）",
    )
    # ── 资源级结果：业务工作流的**主要**成败指标 ──
    # 步骤成功只代表"动作跑完了"，真正代表业务成败的是资源记录（success/failed）。
    # total=0 表示该工作流没有资源记录（纯步骤型），此时运行结论退回步骤级判定。
    resource_total: int = Field(default=0, description="本次运行处理的资源总数")
    resource_success: int = Field(default=0, description="资源处理成功数")
    resource_failed: int = Field(default=0, description="资源处理失败数（下一轮重试）")
    resource_skipped: int = Field(
        default=0, description="资源终态跳过数（如 404，不算失败）"
    )


class WorkflowRunStatusChanged(SQLModel):
    """一次运行的状态迁移事件。

    ``from_status`` 为 ``None`` 表示「新建了运行记录」（此时 ``to_status`` 是初始状态）。
    ``from_status == to_status`` 表示幂等重复写入（订阅者应自行忽略）。
    """

    run_id: str = Field(description="运行ID")
    from_status: WorkflowRunStatusEnum | None = Field(
        default=None, description="迁移前的状态（新建记录时为 None）"
    )
    to_status: WorkflowRunStatusEnum = Field(description="迁移后的状态")
    reason: str | None = Field(default=None, description="迁移原因（日志用）")
    occurred_at: datetime = Field(
        default_factory=datetime.now, description="事件产生时间"
    )
    summary: WorkflowRunSummary | None = Field(
        default=None, description="终态事件附带的结果摘要；非终态为 None"
    )


RunStatusHandler = Callable[[WorkflowRunStatusChanged], Awaitable[None] | None]


def _log_task_error(name: str, task: asyncio.Task) -> None:
    """异步订阅者的异常收敛：事件是旁路，绝不能让异常冒泡到事件循环默认处理器"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"[RunEvent] 订阅者执行失败: {name}, {type(exc).__name__}: {exc}")


class WorkflowRunEventBus:
    """进程内运行状态事件总线（事件循环内单线程安全）。"""

    def __init__(self) -> None:
        self._handlers: list[tuple[str, RunStatusHandler]] = []

    def subscribe(self, name: str, handler: RunStatusHandler) -> None:
        """注册订阅者（同名重复注册以最后一次为准，便于热重载 / 测试重置）"""
        self._handlers = [
            (existing, fn) for existing, fn in self._handlers if existing != name
        ]
        self._handlers.append((name, handler))

    def unsubscribe_all(self) -> None:
        """清空订阅者（测试用）"""
        self._handlers = []

    @property
    def handler_names(self) -> list[str]:
        """已注册订阅者名称（按注册顺序）"""
        return [name for name, _ in self._handlers]

    def publish(self, event: WorkflowRunStatusChanged) -> None:
        """同步发布：同步订阅者当场执行，协程订阅者丢回事件循环执行"""
        for name, handler in list(self._handlers):
            try:
                result = handler(event)
            except Exception as exc:  # noqa: BLE001 - 单个订阅者失败不影响其它
                logger.warning(
                    f"[RunEvent] 订阅者同步执行失败: {name}, "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            if asyncio.iscoroutine(result):
                self._schedule(name, result)

    @staticmethod
    def _schedule(name: str, coro: Awaitable[None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无事件循环（同步脚本 / 已关停）：丢弃异步订阅者，避免 coroutine 泄漏告警
            logger.warning(f"[RunEvent] 无事件循环，跳过异步订阅者: {name}")
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return
        task = loop.create_task(coro, name=f"run-event:{name}")
        task.add_done_callback(lambda t: _log_task_error(name, t))


run_event_bus = WorkflowRunEventBus()


def publish_status_changed(
    run_id: str,
    to_status: WorkflowRunStatusEnum,
    *,
    from_status: WorkflowRunStatusEnum | None = None,
    reason: str | None = None,
    summary: WorkflowRunSummary | None = None,
) -> None:
    """发布运行状态变更事件（旁路：任何异常都不影响状态落库）

    Args:
        summary: 终态事件附带的结果摘要（通知 / 计费订阅者使用）；
            非终态事件传 None。**必须与 ``WorkflowRunStatusChanged.summary``
            保持同一份契约**，否则调用方传参会直接 TypeError（停止工作流曾因此
            在落库之后炸掉，前端拿不到终态帧）。
    """
    if not run_id:
        return
    try:
        run_event_bus.publish(
            WorkflowRunStatusChanged(
                run_id=run_id,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                summary=summary,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 事件是旁路关注点
        logger.warning(f"[RunEvent] 发布运行状态事件失败: run={run_id}, {exc}")


__all__ = [
    "RunStatusHandler",
    "WorkflowRunEventBus",
    "WorkflowRunStatusChanged",
    "WorkflowRunSummary",
    "publish_status_changed",
    "run_event_bus",
]
