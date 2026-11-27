"""会话存活守卫：把「entry 还在池里但浏览器已关闭」统一成可恢复的未启动状态。

导入 / 导出 / 清空都依赖活跃 ``BrowserContext``。浏览器被手动关闭或崩溃后，
``is_closed``（依赖 page 的 close 事件）可能滞后，直接操作会抛
``TargetClosedError``。这里集中处理：

1. 用 context 级 ``cookies()`` 做真实存活探测，失败即清理僵尸会话；
2. 清理走 ``release_browser_session``：把 entry 从会话表移除并推送状态，
   否则会话会一直显示「运行中」，用户点「启动」也会被 create 的
   「会话已存在」误判挡住，等于无法恢复；
3. 需要时重建会话（受启动队列与时长配额约束，由调用方显式决定）。
"""

import contextlib
from dataclasses import dataclass

from loguru import logger

from app.models.common.exceptions.base_exception import BrowserNotStartedException
from app.models.runtime.live_service import BrowserSessionEntry
from app.services.RPA_browser.session.live_service import live_service


@dataclass(frozen=True)
class LaunchAttempt:
    """一次启动尝试：成功给 entry，失败给原因（由调用方转 ``launch_failed``）。"""

    entry: BrowserSessionEntry | None
    error: str


async def get_live_entry(mid: int, browser_id: int) -> BrowserSessionEntry | None:
    """取**活着**的会话；entry 在池里但浏览器已关闭时清理并返回 None。"""
    try:
        entry = live_service.get_browser_session_entry(mid, browser_id)
    except BrowserNotStartedException:
        return None

    if await live_service.is_session_alive(entry):
        return entry

    await discard_session(mid, browser_id)
    return None


async def discard_session(mid: int, browser_id: int) -> None:
    """清理僵尸会话（entry 还在表里，但浏览器进程已关闭）。"""
    with contextlib.suppress(Exception):
        await live_service.release_browser_session(mid, browser_id)


async def try_launch(mid: int, browser_id: int, *, is_vip: bool) -> LaunchAttempt:
    """启动浏览器；失败只返回原因，不抛异常（逐项隔离）。"""
    try:
        entry = await live_service.get_or_create_browser_session_entry(
            mid, browser_id, headless=False, is_vip=is_vip
        )
    except Exception as e:
        logger.warning(f"[会话守卫] 启动浏览器失败: browser_id={browser_id}, {e}")
        return LaunchAttempt(entry=None, error=str(e))
    return LaunchAttempt(entry=entry, error="")


__all__ = ["LaunchAttempt", "discard_session", "get_live_entry", "try_launch"]
