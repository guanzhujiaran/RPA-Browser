"""浏览器操作任务的内存存储与状态流转。

设计边界：

- 任务只落在**进程内存**：RPA 是单进程单 worker（``main.py`` 的 ``uvicorn.run``
  未指定 workers），内存表在进程内天然一致，不需要 DB / Redis；
- 服务重启会丢任务，但操作本身要么已完成、要么被中断，前端轮询会拿到
  「任务不存在或已过期」，不会误以为成功；
- 完成 / 失败后保留 30 分钟供前端轮询取结果，创建新任务时惰性清理过期项；
- 同一浏览器的任务串行执行（per-browser 锁），避免「导入」与「清空」互相打架。
"""

import asyncio
import time
import uuid
from dataclasses import dataclass

from app.models.runtime.browser_operation import (
    ClearLoginStateBatchResponse,
    ImportCookiesBatchResponse,
)
from app.models.runtime.operation_task import (
    OperationTaskStatus,
    OperationTaskStatusResponse,
    OperationTaskType,
)

#: 已完成任务的保留时长（秒）：足够前端轮询取结果，又不会无限占内存
TASK_TTL_SECONDS = 30 * 60


@dataclass
class OperationTask:
    """一个浏览器操作任务（导入 / 清空）的运行态。"""

    task_id: str
    mid: int
    task_type: OperationTaskType
    total: int
    status: OperationTaskStatus = "pending"
    progress: int = 0
    message: str = ""
    error: str = ""
    import_result: ImportCookiesBatchResponse | None = None
    clear_result: ClearLoginStateBatchResponse | None = None
    created_at: int = 0
    updated_at: int = 0


class OperationTaskService:
    """任务表 + 状态流转（全部为内存操作，无 IO）。"""

    _tasks: dict[str, OperationTask] = {}
    #: browser_id -> 锁：同一浏览器的操作串行，避免并发改动登录态
    _browser_locks: dict[int, asyncio.Lock] = {}

    # ============ 生命周期 ============

    @classmethod
    def create(
        cls, mid: int, task_type: OperationTaskType, total: int
    ) -> OperationTask:
        cls._cleanup()
        now = int(time.time())
        task = OperationTask(
            task_id=uuid.uuid4().hex,
            mid=mid,
            task_type=task_type,
            total=total,
            created_at=now,
            updated_at=now,
        )
        cls._tasks[task.task_id] = task
        return task

    @classmethod
    def get(cls, mid: int, task_id: str) -> OperationTask | None:
        """按归属者取任务：他人的 task_id 一律按不存在处理，不泄露存在性。"""
        task = cls._tasks.get(task_id)
        if task is None or task.mid != mid:
            return None
        if cls._is_expired(task):
            cls._tasks.pop(task_id, None)
            return None
        return task

    @classmethod
    def start(cls, task_id: str, message: str = "执行中") -> None:
        task = cls._tasks.get(task_id)
        if task is None:
            return
        task.status = "running"
        task.message = message
        task.updated_at = int(time.time())

    @classmethod
    def update_progress(
        cls, task_id: str, progress: int, message: str = ""
    ) -> None:
        task = cls._tasks.get(task_id)
        if task is None:
            return
        task.progress = max(0, min(progress, task.total))
        if message:
            task.message = message
        task.updated_at = int(time.time())

    @classmethod
    def finish(
        cls,
        task_id: str,
        *,
        import_result: ImportCookiesBatchResponse | None = None,
        clear_result: ClearLoginStateBatchResponse | None = None,
        message: str = "",
    ) -> None:
        task = cls._tasks.get(task_id)
        if task is None:
            return
        task.status = "success"
        task.progress = task.total
        task.import_result = import_result
        task.clear_result = clear_result
        task.message = message or "已完成"
        task.updated_at = int(time.time())

    @classmethod
    def fail(cls, task_id: str, error: str) -> None:
        task = cls._tasks.get(task_id)
        if task is None:
            return
        task.status = "failed"
        task.error = error
        task.message = "执行失败"
        task.updated_at = int(time.time())

    # ============ 并发控制 ============

    @classmethod
    def lock_for(cls, browser_id: int) -> asyncio.Lock:
        """取某个浏览器的操作锁（首次访问时创建）。"""
        lock = cls._browser_locks.get(browser_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._browser_locks[browser_id] = lock
        return lock

    # ============ 查询 ============

    @classmethod
    def snapshot(cls, task: OperationTask) -> OperationTaskStatusResponse:
        return OperationTaskStatusResponse(
            task_id=task.task_id,
            task_type=task.task_type,
            status=task.status,
            progress=task.progress,
            total=task.total,
            message=task.message,
            error=task.error,
            import_result=task.import_result,
            clear_result=task.clear_result,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    # ============ 内部 ============

    @classmethod
    def _cleanup(cls) -> None:
        now = int(time.time())
        for task_id, task in list(cls._tasks.items()):
            if cls._is_expired(task, now):
                cls._tasks.pop(task_id, None)
        # 未被持有的锁可以安全回收，避免 browser_id 无限累积
        for browser_id, lock in list(cls._browser_locks.items()):
            if not lock.locked():
                cls._browser_locks.pop(browser_id, None)

    @staticmethod
    def _is_expired(task: OperationTask, now: int | None = None) -> bool:
        # 未结束的任务不清理（避免前端一直轮询到 not_found）
        if task.status in ("pending", "running"):
            return False
        current = now if now is not None else int(time.time())
        return current - task.updated_at > TASK_TTL_SECONDS


__all__ = ["OperationTask", "OperationTaskService", "TASK_TTL_SECONDS"]
