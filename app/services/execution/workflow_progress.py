"""工作流运行进度上报与 SSE 事件总线

职责：
    1. 执行期埋点 → 单次运行的内存快照（步骤 / 循环轮次 / 计数 / 耗时）；
    2. 快照落库（步骤边界强制、循环轮次节流），作为重启断点恢复的检查点；
    3. 通过进程内事件总线广播 SSE 帧（无订阅者时不构造 JSON）。

范式与 `session_status_bus` 一致：单实例单循环，进程内订阅者表即可；
首帧全量快照 + 心跳注释行 + 终态后关闭，不做 Last-Event-ID 续传。

详见 docs/rpa-workflow-调度外壳计划书.md §10。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from app.config import settings
from app.models.database.workflow.models import (
    WorkflowRunStatusEnum,
)
from app.models.runtime.workflow_progress import (
    WORKFLOW_RUN_LOG_SSE_EVENT_NAME,
    WORKFLOW_RUN_SSE_EVENT_NAME,
    WorkflowRunLogEvent,
    WorkflowRunProgressData,
)
from app.services.RPA_browser.session.session_status_bus import format_sse_frame
from app.utils.json_utils import (
    dumps_json,
    jsonify,
    shrink_json_payload,
    truncate_text,
)

#: 心跳注释行：不产生 data，不会触发前端事件回调
SSE_HEARTBEAT_COMMENT = ": keep-alive"
#: 单连接待发送队列上限：慢消费者只保留最新若干帧。
#: 纯内存队列（消息不落库），给足容量，避免循环体密集上报时丢消息。
_QUEUE_MAX_SIZE = 512
#: 运行消息环形缓冲：新订阅者补发最近 N 条（同样只在内存里，不写盘）
_LOG_BUFFER_SIZE = 1000


@dataclass
class LoopLayer:
    """一层循环的运行时信息（嵌套循环按 depth 分层记录）。

    打印是全量的（每层都发消息），但**进度维度**（两条进度条）只跟踪最外层，
    所以这里按层独立记录轮次，避免内层把外层的 N/N 覆盖掉。
    """

    name: str = ""
    total: int = 0
    index: int = 0
    max_index: int = 0


def build_step_detail(result: object, *, max_length: int | None = None) -> str | None:
    """把一次动作执行结果渲染成可展示的明细文本（运行消息的 `detail`）。

    内容 = 动作返回数据（`ActionResult.data`，JSON 化）
    + 执行过程日志（`ActionResult.logs`，如去重统计），让「运行消息」栏不只显示
    「完成了哪一步」，还能看到「拿到了什么数据」（如 `fetch_external_data` 的响应）。

    **打印与落库是两件事**：这里产出的 `detail` 只随 SSE 事件转发给前端，
    既不写运行记录表也不进操作日志，不占硬盘，所以默认**不裁剪**
    （`workflow_run_log_detail_max_length=0`）。需要给慢网络兜底时才配上限。

    两者都为空时返回 None（前端不渲染明细行）。
    """
    if result is None:
        return None
    limit = (
        settings.workflow_run_log_detail_max_length if max_length is None else max_length
    )
    lines: list[str] = []

    data = getattr(result, "data", None)
    if data is not None:
        lines.append(dumps_json(shrink_json_payload(jsonify(data), limit)))

    raw_logs = getattr(result, "logs", None)
    if isinstance(raw_logs, str):
        lines.append(raw_logs)
    elif isinstance(raw_logs, (list, tuple)):
        lines.extend(str(line) for line in raw_logs if line)

    if not lines:
        return None
    return truncate_text("\n".join(lines), limit)


class WorkflowRunProgressBus:
    """进程内运行进度广播（以 run_id 为键）"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[WorkflowRunProgressData]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定事件循环（lifespan 调用），供非循环线程发布时安全投递。"""
        self._loop = loop

    def subscribe(self, run_id: str) -> asyncio.Queue[WorkflowRunProgressData]:
        queue: asyncio.Queue[WorkflowRunProgressData] = asyncio.Queue(
            maxsize=_QUEUE_MAX_SIZE
        )
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(
        self, run_id: str, queue: asyncio.Queue[WorkflowRunProgressData]
    ) -> None:
        queues = self._subscribers.get(run_id)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            self._subscribers.pop(run_id, None)

    def has_subscribers(self, run_id: str) -> bool:
        return run_id in self._subscribers

    def subscriber_count(self, run_id: str) -> int:
        return len(self._subscribers.get(run_id, ()))

    def publish(self, data: WorkflowRunProgressData) -> None:
        """发布进度快照（无订阅者时直接丢弃，不构造 JSON）。"""
        if not self.has_subscribers(data.run_id):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is None:
                logger.debug(f"运行进度事件无可用事件循环，已丢弃: {data.run_id}")
                return
            self._loop.call_soon_threadsafe(self._publish_now, data)
            return
        self._publish_now(data)

    def _publish_now(self, data: WorkflowRunProgressData) -> None:
        for queue in list(self._subscribers.get(data.run_id, ())):
            self._offer(queue, data)

    @staticmethod
    def _offer(
        queue: asyncio.Queue[WorkflowRunProgressData],
        data: WorkflowRunProgressData,
    ) -> None:
        """投递一帧；队列满则丢最旧帧，保证订阅者最终看到最新状态。"""
        try:
            queue.put_nowait(data)
            return
        except asyncio.QueueFull:
            pass
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("运行进度推送队列持续拥塞，已丢弃最新帧")


class WorkflowRunLogBus:
    """运行消息广播（按 run_id）

    与进度总线分开：进度是「全量快照」（后到者直接看最新状态即可），
    消息是「增量事件」（步骤开始/结束，需要逐条展示且不能被覆盖）。
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[WorkflowRunLogEvent]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, run_id: str) -> asyncio.Queue[WorkflowRunLogEvent]:
        queue: asyncio.Queue[WorkflowRunLogEvent] = asyncio.Queue(
            maxsize=_QUEUE_MAX_SIZE
        )
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(
        self, run_id: str, queue: asyncio.Queue[WorkflowRunLogEvent]
    ) -> None:
        queues = self._subscribers.get(run_id)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            self._subscribers.pop(run_id, None)

    def has_subscribers(self, run_id: str) -> bool:
        return run_id in self._subscribers

    def publish(self, event: WorkflowRunLogEvent) -> None:
        if not self.has_subscribers(event.run_id):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is None:
                return
            self._loop.call_soon_threadsafe(self._publish_now, event)
            return
        self._publish_now(event)

    def _publish_now(self, event: WorkflowRunLogEvent) -> None:
        for queue in list(self._subscribers.get(event.run_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 消息可以丢最旧的（保留最新），但单条事件不阻塞执行
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


workflow_run_progress_bus = WorkflowRunProgressBus()
workflow_run_log_bus = WorkflowRunLogBus()
#: 运行中的上报器（run_id -> reporter）：SSE 建连时补发最近消息
workflow_run_reporters: dict[str, "WorkflowRunProgressReporter"] = {}


def get_run_reporter(run_id: str) -> "WorkflowRunProgressReporter | None":
    return workflow_run_reporters.get(run_id)


class WorkflowRunProgressReporter:
    """单次运行的进度上报器

    由 `run_workflow` 在创建运行记录后构造，随执行链路透传到 `Scope.progress`：
    - `Pipeline.execute`（仅顶层）上报步骤开始/结束；
    - `LoopStep` 每轮上报循环轮次（只记录最外层循环，避免嵌套覆盖）。
    """

    def __init__(
        self,
        data: WorkflowRunProgressData,
        *,
        step_offset: int = 0,
        persist_interval: float | None = None,
    ) -> None:
        self._data = data
        # 断点恢复时从 checkpoint 之后执行，上报的步骤下标需叠加偏移量
        self._step_offset = step_offset
        self._persist_interval = (
            settings.workflow_run_progress_persist_interval
            if persist_interval is None
            else persist_interval
        )
        self._last_persist_at = 0.0
        self._loop_depth = 2**31 - 1
        # 嵌套循环：depth -> 该层状态（打印按层全量，进度维度只跟踪最外层）
        self._loop_layers: dict[int, LoopLayer] = {}
        self._persist_lock = asyncio.Lock()
        # ── 运行消息（步骤级明细）──
        self._log_seq = 0
        self._log_buffer: deque[WorkflowRunLogEvent] = deque(maxlen=_LOG_BUFFER_SIZE)
        self._step_started_at: float | None = None
        # 供 SSE 建连时补发最近消息
        workflow_run_reporters[self._data.run_id] = self

    def history(self) -> list[WorkflowRunLogEvent]:
        """最近的消息（新连接补发用；上限 `_LOG_BUFFER_SIZE`）。"""
        return list(self._log_buffer)

    @property
    def data(self) -> WorkflowRunProgressData:
        return self._data

    # ── 埋点入口 ─────────────────────────────────────────

    def _emit_log(
        self,
        *,
        kind: str,
        text: str,
        level: str = "info",
        detail: str | None = None,
        action_id: str = "",
        action_name: str = "",
        success: bool | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
        loop_index: int = 0,
        loop_total: int = 0,
        depth: int = 0,
    ) -> None:
        """构造并广播一条运行消息（同时写入环形缓冲，供新连接补发）。

        Args:
            depth: 嵌套层级（0=顶层步骤，1=最外层循环体内……），前端据此缩进。
        """
        self._log_seq += 1
        event = WorkflowRunLogEvent(
            run_id=self._data.run_id,
            seq=self._log_seq,
            ts=datetime.now(),
            kind=kind,
            level=level,
            text=text,
            detail=detail,
            action_id=action_id,
            action_name=action_name,
            step_index=self._data.current_step_index,
            total_steps=self._data.total_steps,
            success=success,
            duration_ms=duration_ms,
            error=error,
            loop_index=loop_index,
            loop_total=loop_total,
            depth=depth,
        )
        self._log_buffer.append(event)
        workflow_run_log_bus.publish(event)

    async def step_started(self, index: int, action_id: str, name: str = "") -> None:
        """顶层步骤开始：更新当前步骤并立即推送。

        同时清空循环维度：新步骤意味着上一轮循环已经结束，继续展示旧轮次会让
        进度条停在 100%（N/N）而实际仍在执行后续步骤。
        """
        self._data.current_step_index = self._step_offset + max(index, 0)
        self._data.current_step_name = name or action_id
        self._reset_loop()
        self._data.updated_at = datetime.now()
        self._refresh_duration()
        self._step_started_at = time.monotonic()

        display_index = self._data.current_step_index + 1
        total = self._data.total_steps
        position = f"{display_index}/{total}" if total else str(display_index)
        self._emit_log(
            kind="step_started",
            text=f"开始步骤 {position}：{self._data.current_step_name}",
            action_id=action_id,
            action_name=self._data.current_step_name,
        )

        await self._persist(force=False)
        self._publish()

    async def loop_finished(self, action_id: str, depth: int = 1) -> None:
        """一层循环结束：发消息（每层都发）；结束的是跟踪层时清空循环维度。

        Args:
            depth: 结束的循环所在层级（1=最外层）。内层结束不动进度维度。
        """
        layer = self._loop_layers.pop(depth, None)
        if layer is None:
            return
        finished_name = layer.name or action_id
        # 用「实际执行到的最大轮次」：break/continue 提前退出时最后一轮小于总数
        finished_index = max(layer.index, layer.max_index)
        finished_total = layer.total
        # 只有被跟踪的那层（最外层）结束才把进度维度回落，内层结束不影响进度条
        if depth <= self._loop_depth:
            self._reset_loop()
        self._data.updated_at = datetime.now()
        self._refresh_duration()
        if finished_total and finished_index < finished_total:
            # 没跑满：明确写实际轮次，避免界面显示「共 35 轮」而实际只处理了 2 条
            rounds = f"执行 {finished_index}/{finished_total} 轮，提前退出"
        elif finished_total:
            rounds = f"共 {finished_total} 轮"
        else:
            rounds = f"共 {finished_index} 轮"
        self._emit_log(
            kind="loop_finished",
            text=f"循环「{finished_name}」结束（{rounds}）",
            action_id=action_id,
            action_name=finished_name,
            loop_index=finished_index,
            loop_total=finished_total,
            depth=depth,
        )
        await self._persist(force=True)
        self._publish()

    def _reset_loop(self) -> None:
        self._data.loop_action_name = ""
        self._data.loop_index = 0
        self._data.loop_total = 0
        self._data.loop_total_known = True
        self._data.loop_body_completed = 0
        self._data.loop_body_total = 0
        self._data.loop_body_step_name = ""
        self._loop_depth = 2**31 - 1
        self._loop_layers.clear()

    async def step_finished(
        self,
        success: bool,
        error: str | None = None,
        *,
        result: object = None,
    ) -> None:
        """顶层步骤结束：完成数 / 成功失败计数递增，步骤边界强制落库。

        Args:
            success: 步骤是否成功。
            error: 失败原因。
            result: 步骤的 `ActionResult`；传入时把动作返回内容渲染成 `detail`
                一并推送（如 `fetch_external_data` 的接口响应）。
        """
        self._data.completed_steps += 1
        if success:
            self._data.success_count += 1
        else:
            self._data.failed_count += 1
        self._data.updated_at = datetime.now()
        self._refresh_duration()

        duration_ms = (
            (time.monotonic() - self._step_started_at) * 1000
            if self._step_started_at is not None
            else None
        )
        self._step_started_at = None
        name = self._data.current_step_name
        total = self._data.total_steps
        position = f"{self._data.completed_steps}/{total}" if total else str(
            self._data.completed_steps
        )
        cost = f"，耗时 {duration_ms / 1000:.1f}s" if duration_ms is not None else ""
        if success:
            text = f"完成步骤 {position}：{name}{cost}"
        else:
            text = f"步骤 {position} 失败：{name}{cost}｜{error or '未知错误'}"
        self._emit_log(
            kind="step_finished",
            text=text,
            level="success" if success else "error",
            detail=build_step_detail(result),
            action_id=name,
            action_name=name,
            success=success,
            duration_ms=duration_ms,
            error=error,
        )

        await self._persist(force=True)
        self._publish()

    async def loop_iteration(
        self,
        *,
        action_id: str,
        name: str,
        index: int,
        total: int,
        depth: int,
        body_total: int = 0,
    ) -> None:
        """循环轮次开始（**每层都发消息**，进度维度只跟踪最外层）。

        Args:
            index: 当前轮次（1 基）。
            total: 总轮次；0 表示不可知（condition 循环 / 分母运行时才求出）。
            depth: 循环嵌套深度（1=最外层）。打印按层全量，进度条只跟踪最外层。
            body_total: 循环体内的动作总数（第二条进度条的分母）。
        """
        loop_name = name or action_id
        layer = self._loop_layers.setdefault(depth, LoopLayer(name=loop_name))
        layer.name = loop_name
        layer.total = total
        layer.index = index
        layer.max_index = max(layer.max_index, index)

        # 进度维度：只采纳更浅（更外层）的循环，避免内层把外层的 N/N 覆盖掉
        if depth <= self._loop_depth:
            self._loop_depth = depth
            self._data.loop_action_name = loop_name
            self._data.loop_index = index
            self._data.loop_total = total
            self._data.loop_total_known = total > 0
            # 新一轮：循环体内动作进度归零
            self._data.loop_body_completed = 0
            self._data.loop_body_total = body_total
            self._data.loop_body_step_name = ""
            self._data.updated_at = datetime.now()
            self._refresh_duration()

        text = (
            f"第 {index}/{total} 轮 · 循环「{loop_name}」"
            if total > 0
            else f"已执行 {index} 轮 · 循环「{loop_name}」"
        )
        self._emit_log(
            kind="loop_iteration",
            text=text,
            action_id=action_id,
            action_name=loop_name,
            loop_index=index,
            loop_total=total,
            depth=depth,
        )

        await self._persist(force=False)
        self._publish()

    async def loop_body_step(
        self, completed: int, total: int, step_name: str = "", depth: int = 1
    ) -> None:
        """本轮循环体内的动作进度（第二条进度条）。

        只更新**被跟踪的那层**（最外层）的进度；嵌套内层的动作不参与进度条，
        但消息照发（打印与进度是两件事）。

        Args:
            completed: 本轮已完成的动作数。
            total: 本轮动作总数（0 表示未知/非循环体）。
            step_name: 当前动作名。
            depth: 所在层级。
        """
        if depth != self._loop_depth or not self._data.loop_action_name:
            return
        if total <= 0:
            return
        self._data.loop_body_completed = max(0, completed)
        self._data.loop_body_total = total
        self._data.loop_body_step_name = step_name
        self._data.updated_at = datetime.now()
        self._refresh_duration()
        await self._persist(force=False)
        self._publish()

    # ── 循环体内动作的运行消息 ─────────────────────────

    async def body_step_started(
        self, index: int, total: int, step_name: str = "", *, depth: int = 1
    ) -> None:
        """循环体内一个动作开始：更新「本轮动作」进度并发一条消息。

        只发消息，不进 `completed_steps` / 成功失败计数（循环整体才算一个顶层步骤）。
        嵌套多少层都发，前端按 `depth` 逐级缩进展示。
        """
        await self.loop_body_step(index, total, step_name, depth=depth)
        layer = self._loop_layers.get(depth)
        position = f"{index + 1}/{total}" if total else str(index + 1)
        self._emit_log(
            kind="body_step_started",
            text=f"第 {layer.index if layer else 0} 轮 · 动作 {position}：{step_name}",
            action_id=step_name,
            action_name=step_name,
            loop_index=layer.index if layer else 0,
            loop_total=layer.total if layer else 0,
            depth=depth,
        )

    async def body_step_finished(
        self,
        index: int,
        total: int,
        step_name: str = "",
        success: bool = True,
        error: str | None = None,
        *,
        result: object = None,
        duration_ms: float | None = None,
        depth: int = 1,
    ) -> None:
        """循环体内一个动作结束：更新进度并发一条消息。

        消息只走 SSE、不落库，因此**全量打印**（嵌套层也一样）：成功/失败都附
        `detail`（动作返回内容），由前端按轮次分批收拢、按层级嵌套展示。
        """
        await self.loop_body_step(index, total, step_name, depth=depth)
        layer = self._loop_layers.get(depth)
        round_index = layer.index if layer else 0
        round_total = layer.total if layer else 0
        position = f"{index}/{total}" if total else str(index)
        cost = f"，耗时 {duration_ms / 1000:.1f}s" if duration_ms is not None else ""
        if success:
            text = f"第 {round_index} 轮 · 动作 {position}：{step_name}{cost}"
        else:
            text = (
                f"第 {round_index} 轮 · 动作 {position} 失败：{step_name}"
                f"{cost}｜{error or '未知错误'}"
            )
        detail = build_step_detail(result)
        self._emit_log(
            kind="body_step_finished",
            text=text,
            level="success" if success else "error",
            detail=detail,
            action_id=step_name,
            action_name=step_name,
            success=success,
            duration_ms=duration_ms,
            error=error,
            loop_index=round_index,
            loop_total=round_total,
            depth=depth,
        )

    async def flush(
        self,
        *,
        status: WorkflowRunStatusEnum | str,
        error_message: str | None = None,
        duration_ms: float | None = None,
    ) -> WorkflowRunProgressData:
        """运行收口：写入终态并推送终态帧（SSE 端收到后关闭连接）。"""
        self._data.status = str(status)
        self._data.error_message = error_message
        if duration_ms is not None:
            self._data.duration_ms = duration_ms
        else:
            self._refresh_duration()
        self._data.updated_at = datetime.now()
        if self._data.is_terminal:
            self._data.finished_at = datetime.now()

        # 先发「运行结束」消息，再发终态进度（终态进度会让 SSE 关闭连接）
        status_value = self._data.status
        level = {
            "success": "success",
            "failed": "error",
            "interrupted": "warn",
            "cancelled": "warn",
        }.get(status_value, "info")
        summary = (
            f"运行结束：{status_value}"
            f"（成功 {self._data.success_count} / 失败 {self._data.failed_count}"
            f" / 共 {self._data.total_steps} 步）"
        )
        if error_message:
            summary = f"{summary}｜{error_message}"
        self._emit_log(
            kind="run_finished",
            text=summary,
            level=level,
            success=status_value == "success",
            error=error_message,
        )

        await self._persist(force=True)
        self._publish()
        # 运行已结束：不再需要补发缓冲（晚到的订阅者看 DB 快照即可）
        workflow_run_reporters.pop(self._data.run_id, None)
        return self._data

    # ── 内部：落库与推送 ─────────────────────────────────

    def _refresh_duration(self) -> None:
        elapsed = (datetime.now() - self._data.started_at).total_seconds() * 1000
        self._data.duration_ms = max(elapsed, 0.0)

    async def _persist(self, *, force: bool) -> None:
        """落库快照；循环轮次按最小间隔节流，步骤边界与终态强制写。"""
        now = time.monotonic()
        if not force and now - self._last_persist_at < self._persist_interval:
            return

        # 同一 run 的并发写合并：拿不到锁说明已有写在进行，直接跳过本次（最新快照仍会被后续写入）
        if self._persist_lock.locked():
            return
        async with self._persist_lock:
            self._last_persist_at = now
            try:
                from app.services.execution.crud_service.workflow_run_crud import (
                    workflow_run_crud_svr,
                )

                await workflow_run_crud_svr.update_progress(
                    run_id=self._data.run_id,
                    completed_steps=self._data.completed_steps,
                    current_step_index=self._data.current_step_index,
                    current_step_name=self._data.current_step_name,
                    loop_action_name=self._data.loop_action_name,
                    loop_index=self._data.loop_index,
                    loop_total=self._data.loop_total,
                    loop_body_completed=self._data.loop_body_completed,
                    loop_body_total=self._data.loop_body_total,
                    loop_body_step_name=self._data.loop_body_step_name,
                    success_count=self._data.success_count,
                    failed_count=self._data.failed_count,
                )
            except Exception as exc:  # noqa: BLE001 - 进度是旁路关注点，绝不打断执行
                logger.warning(
                    f"[WorkflowProgress] 进度落库失败（不阻断执行）: "
                    f"run={self._data.run_id}, error={exc}"
                )

    def _publish(self) -> None:
        workflow_run_progress_bus.publish(self._data)


async def iter_workflow_run_events(
    initial: WorkflowRunProgressData,
    *,
    heartbeat_interval: float,
    backlog: list[WorkflowRunLogEvent] | None = None,
) -> AsyncIterator[str]:
    """产出 SSE 文本帧：进度快照 + 运行消息 + 心跳；终态帧后结束。

    同一条连接承载两类事件：
      - `workflow_run_progress`：全量进度快照（覆盖式，看到最新即可）；
      - `workflow_run_log`：步骤级明细（增量，逐条展示在「运行消息」栏）。

    Args:
        backlog: 建连时补发的最近消息（上报器仍在内存时提供），避免半路打开
            只看到后续步骤。

    客户端断开时生成器被关闭，`finally` 中反订阅，订阅者表不会残留。
    """
    run_id = initial.run_id
    progress_queue = workflow_run_progress_bus.subscribe(run_id)
    log_queue = workflow_run_log_bus.subscribe(run_id)
    logger.info(
        f"工作流运行 SSE 已连接: run={run_id} "
        f"当前连接数={workflow_run_progress_bus.subscriber_count(run_id)}"
    )
    try:
        terminal_at_connect = initial.is_terminal
        yield format_sse_frame(
            WORKFLOW_RUN_SSE_EVENT_NAME, initial.model_dump_json()
        )
        for event in backlog or []:
            yield format_sse_frame(
                WORKFLOW_RUN_LOG_SSE_EVENT_NAME, event.model_dump_json()
            )
        # 建连时运行已结束：首帧即终态，无需继续占用连接。
        # 注意用建连瞬间的快照判断：`initial` 可能是与上报器共享的活对象，
        # 生成器挂起期间它的状态会被后续进度改写。
        if terminal_at_connect:
            return
        while True:
            progress_task = asyncio.create_task(progress_queue.get())
            log_task = asyncio.create_task(log_queue.get())
            done, pending = await asyncio.wait(
                {progress_task, log_task},
                timeout=heartbeat_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if not done:
                yield f"{SSE_HEARTBEAT_COMMENT}\n\n"
                continue

            terminal = False
            if progress_task in done:
                data = progress_task.result()
                yield format_sse_frame(
                    WORKFLOW_RUN_SSE_EVENT_NAME, data.model_dump_json()
                )
                terminal = data.is_terminal
            if log_task in done:
                event = log_task.result()
                yield format_sse_frame(
                    WORKFLOW_RUN_LOG_SSE_EVENT_NAME, event.model_dump_json()
                )
            if terminal:
                # 终态帧之前可能还排着一条 run_finished 消息（先发消息、后发终态），
                # 这里排空剩余消息再关闭，保证「运行结束」一定可见。
                while True:
                    try:
                        pending_event = log_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield format_sse_frame(
                        WORKFLOW_RUN_LOG_SSE_EVENT_NAME,
                        pending_event.model_dump_json(),
                    )
                return
    finally:
        workflow_run_progress_bus.unsubscribe(run_id, progress_queue)
        workflow_run_log_bus.unsubscribe(run_id, log_queue)
        logger.info(
            f"工作流运行 SSE 已断开: run={run_id} "
            f"剩余连接数={workflow_run_progress_bus.subscriber_count(run_id)}"
        )