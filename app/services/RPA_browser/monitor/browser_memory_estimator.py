"""浏览器内存实测估算 - 用真实进程占用校准启动准入额度

**为什么需要**
单个 Chromium 会话的内存开销并不固定：它随指纹参数、页面数量、页面复杂度、
WebRTC 拉流与否而变化，写死一个常量要么过于保守（浪费内存）、要么过于乐观（并发启动把内存打爆）。
本模块直接**实测**每个会话的真实占用，反馈给启动队列用作内存预留额度。

**怎么识别**
每个会话使用独立的 ``--user-data-dir``（``{user_data_dir}/{mid}/{browser_id}``），
Chromium 主进程与所有子进程（renderer / gpu / utility）都会带该参数，
据此可以把进程按会话聚合，无需依赖任何内部属性或额外埋点。

**口径（只统计「不可回收」的匿名内存）**
累加 ``/proc/<pid>/smaps_rollup`` 的 ``Pss_Anon + Pss_Shmem``：

- ``Pss_Anon`` = 进程真实占用（堆 / JS 堆 / 合成与 GPU 缓冲），内存吃紧时**无法回收**，
  正是 OOM 判定与准入记账该关心的部分；
- ``Pss_Shmem`` = 共享匿名页（如 GPU 共享缓冲），按 ``Pss`` 比例分摊，
  避免 browser / gpu / renderer 多进程共享同一块内存时被重复计算；
- **明确排除 ``Pss_File``**（文件映射：AppImage 里的 Chromium 二进制 / .so / 字体 / ICU 数据）：
  它们是干净的 file-backed 页，内存吃紧时内核可直接丢弃，不该计入「单实例预留额度」。
  否则额度会虚高近一倍 —— 本机实测同一实例：``Pss`` 合计 ≈770MB，匿名口径只有 ≈420MB。

读不到 ``smaps_rollup`` 时回退 ``/proc/<pid>/status`` 的 ``RssAnon``，最后才回退整块 ``RSS``。

本机实测参考（有头 Chromium + Xvfb + AppImage 安装，1920x1080，2026-09-25 复核；
含一路 WebRTC 推流，11 个进程 = browser + gpu + renderer×3 + utility×2 + zygote×3）：
- 匿名口径合计 **≈420MB**（renderer ≈154MB、gpu-process ≈144MB、browser 主进程 ≈51MB）
- 同期同实例另两种口径（仅供对照，**不可混用**）：``Pss`` 合计 ≈770MB、私有内存 ≈532MB
- 同一会话多开 page 时约每个 +15~20MB；会话关闭后进程归零，无残留
"""

import math
import os
import time
from collections import deque
from statistics import mean

import psutil
from loguru import logger

from app.config import CONF, settings
from app.models.runtime.launch_queue import (
    BrowserMemoryEstimatorStatus,
    BrowserMemorySample,
)

# Chromium 系进程名特征（含 AppImage 形式的 ungoogled-chromium）
_CHROMIUM_NAME_HINTS = ("chrome", "chromium")

# 用户数据目录参数前缀
_USER_DATA_DIR_ARG = "--user-data-dir="


class BrowserMemoryEstimator:
    """浏览器单实例内存占用的实测估算器"""

    def __init__(self) -> None:
        self._samples: deque[float] = deque(
            maxlen=max(1, settings.browser_memory_sample_window)
        )
        self._peak_mb: float | None = None
        self._active_instances: int = 0
        self._last_scan_monotonic: float = 0.0

    # ────────────────────────── 采样 ──────────────────────────

    def scan(self) -> list[BrowserMemorySample]:
        """扫描当前所有浏览器会话的内存占用（阻塞调用，请放线程池执行）

        Returns:
            按 ``--user-data-dir`` 聚合后的会话样本列表
        """
        base_dir = str(CONF.Path.user_data_dir).rstrip("/")
        # 会话目录 -> 进程 PID 列表
        grouped: dict[str, list[int]] = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if not any(hint in name for hint in _CHROMIUM_NAME_HINTS):
                    continue
                cmdline = proc.cmdline()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

            session_dir = self._match_session_dir(cmdline, base_dir)
            if session_dir is None:
                continue
            grouped.setdefault(session_dir, []).append(proc.info["pid"])

        now = int(time.time())
        samples: list[BrowserMemorySample] = []
        for session_dir, pids in grouped.items():
            total_mb = round(sum(self._process_anon_mb(pid) for pid in pids), 1)
            # 过滤噪声：调用方传入的目录可能只是被别的进程引用，实际没有浏览器起来
            if total_mb < settings.browser_memory_min_sample_mb:
                continue
            samples.append(
                BrowserMemorySample(
                    user_data_dir=session_dir,
                    total_mb=total_mb,
                    process_count=len(pids),
                    sampled_at=now,
                )
            )

        return samples

    def refresh(self) -> BrowserMemoryEstimatorStatus:
        """重新采样一次并更新统计（阻塞调用，请放线程池执行）"""
        try:
            samples = self.scan()
        except Exception as e:
            logger.error(f"浏览器内存采样失败，沿用上次统计: {e}")
            self._last_scan_monotonic = time.monotonic()
            return self.status()

        for sample in samples:
            self._samples.append(sample.total_mb)
            if self._peak_mb is None or sample.total_mb > self._peak_mb:
                self._peak_mb = sample.total_mb

        self._active_instances = len(samples)
        self._last_scan_monotonic = time.monotonic()

        if samples:
            logger.debug(
                f"浏览器内存采样: 实例数={len(samples)}, "
                f"单实例={[s.total_mb for s in samples]}MB, "
                f"预留额度={self.reserved_mb()}MB"
            )
        return self.status()

    def seconds_since_scan(self) -> float:
        """距离上次采样过去的秒数（从未采样时返回 inf）"""
        if self._last_scan_monotonic <= 0:
            return float("inf")
        return time.monotonic() - self._last_scan_monotonic

    # ────────────────────────── 估算结果 ──────────────────────────

    def active_instances(self) -> int:
        """最近一次扫描到的活跃浏览器实例数（可能滞后一个扫描周期）"""
        return self._active_instances

    def reserved_mb(self) -> int:
        """准入记账用的单实例预留额度(MB)

        取 ``max(配置基准值, 实测 P90 × 安全系数)``：
        - 实测偏低时不激进（基准值兜底，留有余量）；
        - 实测偏高时（重页面 / WebRTC 拉流）自动上浮，防止并发启动打爆内存。
        样本不足或未启用实测时退回配置基准值。
        """
        configured = settings.browser_launch_reserved_memory_mb
        if not settings.browser_memory_estimate_enabled:
            return configured

        samples = sorted(self._samples)
        if len(samples) < max(1, settings.browser_memory_min_samples):
            return configured

        p90 = self._percentile(samples, 0.9)
        measured = math.ceil(p90 * settings.browser_memory_safety_factor)
        # 对齐到 16MB，避免额度抖动导致放行节奏跳变
        measured = int(math.ceil(measured / 16) * 16)
        return max(configured, measured)

    def estimated_peak_instances(self, memory_mb: int) -> int:
        """给定可用内存时，理论上还能容纳多少个浏览器实例（仅供参考）"""
        reserved = self.reserved_mb()
        if reserved <= 0:
            return 0
        return max(0, int(memory_mb // reserved))

    def status(self) -> BrowserMemoryEstimatorStatus:
        """当前估算状态（供接口 / 监控展示）"""
        samples = list(self._samples)
        return BrowserMemoryEstimatorStatus(
            enabled=settings.browser_memory_estimate_enabled,
            sample_count=len(samples),
            window=self._samples.maxlen or 0,
            min_samples=settings.browser_memory_min_samples,
            active_instances=self._active_instances,
            last_sample_mb=round(samples[-1], 1) if samples else None,
            average_mb=round(mean(samples), 1) if samples else None,
            peak_mb=round(self._peak_mb, 1) if self._peak_mb is not None else None,
            reserved_mb=self.reserved_mb(),
            configured_reserved_mb=settings.browser_launch_reserved_memory_mb,
        )

    # ────────────────────────── 内部实现 ──────────────────────────

    @staticmethod
    def _match_session_dir(cmdline: list[str], base_dir: str) -> str | None:
        """从命令行参数中提取本项目会话的用户数据目录

        同时按原路径与 realpath 匹配，避免配置路径中夹带符号链接时漏采。
        """
        base_real = os.path.realpath(base_dir)
        for arg in cmdline:
            if not arg.startswith(_USER_DATA_DIR_ARG):
                continue
            path = arg[len(_USER_DATA_DIR_ARG) :].rstrip("/")
            if path == base_dir or path.startswith(base_dir + os.sep):
                return path
            if path == base_real or path.startswith(base_real + os.sep):
                return path
        return None

    @staticmethod
    def _process_anon_mb(pid: int) -> float:
        """读取进程「不可回收」内存(MB)：``Pss_Anon + Pss_Shmem``。

        只算匿名页与共享匿名页：文件映射页（.so / 字体 / AppImage 里的二进制）是干净的
        可回收页，内存吃紧时内核直接丢弃，计入预留额度会让准入额度虚高（实测虚高近一倍）。
        共享匿名页按 PSS 比例分摊，避免多进程共享缓冲被重复计算。

        读不到 ``smaps_rollup`` 时回退 ``/proc/<pid>/status`` 的 ``RssAnon``，
        最后才回退整块 ``RSS``（含文件页，偏保守）。
        """
        try:
            anon_mb: float | None = None
            shmem_mb = 0.0
            with open(f"/proc/{pid}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Pss_Anon:"):
                        anon_mb = int(line.split()[1]) / 1024
                    elif line.startswith("Pss_Shmem:"):
                        shmem_mb = int(line.split()[1]) / 1024
            if anon_mb is not None:
                return anon_mb + shmem_mb
        except (OSError, ValueError, IndexError):
            pass

        # 回退 1：匿名 RSS（同样不含可回收的文件页）
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("RssAnon:"):
                        return int(line.split()[1]) / 1024
        except (OSError, ValueError, IndexError):
            pass

        # 回退 2：整块 RSS（含文件页，会偏高，仅当上面两种都读不到时才用）
        try:
            return psutil.Process(pid).memory_info().rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return 0.0

    @staticmethod
    def _percentile(sorted_values: list[float], ratio: float) -> float:
        """线性插值百分位"""
        if not sorted_values:
            return 0.0
        if len(sorted_values) == 1:
            return sorted_values[0]
        position = (len(sorted_values) - 1) * ratio
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return sorted_values[int(position)]
        weight = position - lower
        return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


# 全局单例
browser_memory_estimator = BrowserMemoryEstimator()


__all__ = ["BrowserMemoryEstimator", "browser_memory_estimator"]
