"""系统内存监控 - 为浏览器启动准入提供内存快照

浏览器启动的内存代价较高，启动前必须先确认系统可用内存是否充足。
本模块只做「读快照」这一件事，具体的准入策略由启动队列
（``app.services.RPA_browser.session.launch_queue``）决定。
"""

import psutil
from loguru import logger

from app.config import settings
from app.models.runtime.launch_queue import MemorySnapshot

_MB = 1024 * 1024


class MemoryMonitor:
    """系统内存监控（无状态，可安全复用）"""

    def snapshot(self) -> MemorySnapshot:
        """读取当前系统内存快照"""
        try:
            vm = psutil.virtual_memory()
        except Exception as e:
            # 读取失败时按「内存充足」处理，避免监控异常导致浏览器完全无法启动
            logger.error(f"读取系统内存失败，按内存充足处理: {e}")
            required = settings.browser_launch_min_available_memory_mb
            return MemorySnapshot(
                total_mb=0,
                available_mb=required,
                used_mb=0,
                used_percent=0.0,
                required_mb=required,
                can_launch=True,
            )

        required = settings.browser_launch_min_available_memory_mb
        available_mb = int(vm.available // _MB)
        return MemorySnapshot(
            total_mb=int(vm.total // _MB),
            available_mb=available_mb,
            used_mb=int(vm.used // _MB),
            used_percent=round(float(vm.percent), 2),
            required_mb=required,
            can_launch=available_mb >= required,
        )

    def available_mb(self) -> int:
        """当前可用内存(MB)"""
        return self.snapshot().available_mb


# 全局单例（无状态，直接复用）
memory_monitor = MemoryMonitor()


__all__ = ["MemoryMonitor", "memory_monitor"]
