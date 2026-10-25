"""
工作流运行取消注册表（进程内停止标记）

用户在运行期间点击「停止」时，路由把 run_id 标记为已请求停止，随后**立刻**对运行
task 调用 ``cancel()`` 强制打断当前 ``await``（见 ``docs/工作流停止计划书.md``）：
``WorkflowRunner`` 的 ``except BaseException`` 收口为 ``INTERRUPTED``，末尾再依据本
注册表的标记改写为 ``CANCELLED``。

标记的第二个用途是兜底：执行器（Pipeline / LoopStep）仍会在**步骤边界**检查该标记
（``scope.is_cancelled()``），这样即使某个调用方只置标记、未直接 ``cancel``，也能在
下一步边界自然停下；超过宽限期再由 ``enforce_stop_grace`` 强制终止。

适用范围：进程内有效。手动运行走 HTTP 协程、定时运行走 AsyncIOScheduler，二者共享
同一事件循环（单实例单循环），因此同一份内存注册表对两种触发方式都生效。多实例
部署时需改用 Redis / DB 等跨进程通道，此处不引入额外依赖。
"""

from __future__ import annotations

import time


class WorkflowCancellationRegistry:
    """以 run_id 为键的停止请求注册表（事件循环内单线程安全）。

    同时记录**请求停止的时刻**（单调时钟），供两阶段停止的第二阶段判断是否已过
    宽限期——借鉴 Docker `stop` 的 SIGTERM→宽限期→SIGKILL：先协作式取消，宽限期内
    让执行器在步骤边界自然收口；超时后由监督者对运行 task 强制 ``cancel()``。
    """

    def __init__(self) -> None:
        self._cancelled: dict[str, float] = {}

    def request_cancel(self, run_id: str) -> bool:
        """标记某次运行请求停止。

        Returns:
            True 表示本次新增了停止请求；False 表示此前已请求过（幂等）。
        """
        if not run_id:
            return False
        if run_id in self._cancelled:
            return False
        self._cancelled[run_id] = time.monotonic()
        return True

    def is_cancelled(self, run_id: str) -> bool:
        """该运行是否已被请求停止。"""
        return bool(run_id) and run_id in self._cancelled

    def cancel_requested_at(self, run_id: str) -> float | None:
        """请求停止的单调时钟时刻；未请求过时返回 None。"""
        return self._cancelled.get(run_id)

    def clear(self, run_id: str) -> None:
        """运行结束后清理标记（与 request_cancel 配对，避免键残留）。"""
        self._cancelled.pop(run_id, None)


cancellation_registry = WorkflowCancellationRegistry()

__all__ = ["WorkflowCancellationRegistry", "cancellation_registry"]
