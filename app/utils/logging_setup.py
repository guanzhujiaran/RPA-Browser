"""全局日志配置：loguru 作为唯一业务日志出口，并把标准库日志一并收敛。

背景：`browsers` 包在 import 时对 root logger 调用了
`logging.basicConfig(level=INFO)`，于是 aiortc / aioice / asyncio / apscheduler
等库的 INFO / WARNING 全部直连 stdout —— WebRTC 建一次连就能刷出上百行。

这里统一收敛：

- 业务日志：loguru 单 sink，级别取 `settings.effective_log_level`
  （dev 默认 INFO 不打 DEBUG，prod 默认 WARNING），可用环境变量 `LOG_LEVEL` 覆盖；
- 标准库日志：经 `_InterceptHandler` 转发给 loguru，按同一级别过滤、同一格式输出；
- 高噪声第三方 logger：单独抬高级别门槛，避免刷屏。
"""

import contextlib
import inspect
import logging
import sys
import types
from typing import Final

from loguru import logger

from app.config import settings

_LOG_FORMAT: Final[str] = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)

# 高噪声第三方 logger 的级别门槛（未列出的一律跟随 root 的 WARNING）
_NOISY_LOGGERS: Final[tuple[tuple[str, int], ...]] = (
    # 写 SSE / 流式响应时客户端断开会触发 asyncio 的 "socket.send() raised exception."
    ("asyncio", logging.ERROR),
    # ICE 候选探测与状态流转每次建连都刷几十行，只保留告警
    ("aioice", logging.WARNING),
    ("aiortc", logging.WARNING),
    # 定时任务每次执行都会打 "Running job ... executed successfully"
    ("apscheduler", logging.WARNING),
    ("urllib3", logging.WARNING),
    ("httpx", logging.WARNING),
    ("httpcore", logging.WARNING),
)


class _InterceptHandler(logging.Handler):
    """把标准库 logging 记录转交 loguru，使全进程日志格式与级别保持一致。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 向上跳过 logging 自身的栈帧，让 loguru 显示真实业务代码的 name:line
        frame: types.FrameType | None = inspect.currentframe()
        depth = 0
        while frame is not None and (
            depth == 0 or frame.f_code.co_filename == logging.__file__
        ):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging() -> None:
    """初始化进程级日志（应用启动时调用一次即可）。"""
    effective_level = settings.effective_log_level

    # 只移除 loguru 的默认 sink（id=0），保留 @log_class_decorator 已注册的文件 sink
    with contextlib.suppress(ValueError):
        logger.remove(0)

    logger.add(
        sys.stderr,
        level=effective_level,
        format=_LOG_FORMAT,
        colorize=True,
        backtrace=False,
        # 不打印每帧的局部变量，异常日志只保留「调用链 + 异常本身」
        diagnose=False,
    )

    # 应用日志走 loguru（标准库 logging 没有被业务代码使用），因此 root 平时只放行
    # WARNING —— 第三方库的 INFO 一律不进 loguru；只有 LOG_LEVEL=DEBUG 时才全量放开。
    # force=True：覆盖 browsers 包已经装到 root 上的 INFO handler。
    logging.basicConfig(
        level=logging.DEBUG if effective_level == "DEBUG" else logging.WARNING,
        handlers=[_InterceptHandler()],
        force=True,
    )
    for name, level in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(level)

    logger.info(
        f"日志初始化完成: level={effective_level}, "
        f"运行模式={settings.RUNNING_MODE.value}"
    )
    # 全量配置只在 DEBUG 下打印，避免每次启动刷一屏
    logger.debug(f"Settings loaded\n{settings}")


__all__ = ["setup_logging"]
