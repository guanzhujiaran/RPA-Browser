"""
工作流运行任务注册表（进程内 run_id → asyncio.Task）

把「一次工作流运行」封装成一个后台 ``asyncio.Task`` 后，需要一个地方持有它的
句柄，才能：
    - 判断某次运行在本实例**是否还活着**（僵尸收口的存活真值）；
    - 在宽限期超时后对其 ``cancel()`` 强制终止（两阶段停止的第二阶段）。

借鉴 Docker 容器的监督者模型：容器句柄由守护进程持有（而非容器自身），守护进程
负责在容器停止后统一收尾。这里同样由注册表持有 task，运行结束后经
``WorkflowRunner`` 的 done_callback 统一清理与幂等收口。

适用范围：进程内有效。手动运行与定时运行共享同一事件循环（单实例单循环），故
同一份注册表对两种触发方式都生效。多实例部署时须改用跨进程通道，此处不引入额外
依赖。
"""

from __future__ import annotations

import asyncio


class WorkflowRunTaskRegistry:
    """以 run_id 为键的运行 task 注册表（事件循环内单线程安全）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def register(self, run_id: str, task: asyncio.Task) -> None:
        """登记运行的 task（覆盖同键旧值）。"""
        if run_id:
            self._tasks[run_id] = task

    def get(self, run_id: str) -> asyncio.Task | None:
        """获取运行的 task；不存在时返回 None。"""
        return self._tasks.get(run_id)

    def is_alive(self, run_id: str) -> bool:
        """该运行在本实例是否仍有未结束的 task（僵尸收口的存活真值）。"""
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    def cancel(self, run_id: str) -> bool:
        """对运行的 task 发起强制取消（等价 SIGKILL）。

        Returns:
            True 表示确实找到并请求取消了一个存活的 task；False 表示无存活 task。
        """
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def clear(self, run_id: str) -> None:
        """运行结束后清理句柄（与 register 配对，避免键残留）。"""
        self._tasks.pop(run_id, None)

    def live_run_ids(self) -> list[str]:
        """当前仍有存活 task 的 run_id 列表（供宽限超时扫描）。"""
        return [rid for rid, task in self._tasks.items() if not task.done()]


workflow_run_tasks = WorkflowRunTaskRegistry()

__all__ = ["WorkflowRunTaskRegistry", "workflow_run_tasks"]
