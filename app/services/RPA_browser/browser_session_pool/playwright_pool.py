import asyncio
from typing import Dict, Optional
from app.models.runtime.session import (
    SessionCreateParams,
    BrowserSessionRemoveParams,
    SessionAllCloseResponse,
    SessionCloseResponse,
)
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    BrowserSession,
    PluginedSessionInfo,
)
import time
from loguru import logger
from app.models.exceptions.base_exception import BrowserNotStartedException
import contextlib


class PlaywrightSessionPool:
    """
    管理 BaseUndetectedPlaywright 会话的池化系统

    支持以下功能：
    1. 根据 mid 快速查找现有浏览器会话
    2. 自动创建新的浏览器实例
    3. 会话生命周期管理
    4. 并发安全访问
    """

    def __init__(self):
        # 存储活跃会话的字典，键为mid，值为BrowserSession
        self._active_sessions: Dict[int, BrowserSession] = {}

        # 全局锁，用于保护会话池操作
        self._pool_lock = asyncio.Lock()

    async def get_session(self, params: SessionCreateParams) -> PluginedSessionInfo:
        """
        优先获取
        获取指定mid的浏览器会话，如果不存在则创建新的
        """
        # 先尝试获取现有的会话
        async with self._pool_lock:
            if browser_session := self._active_sessions.get(params.mid):
                if session_info := browser_session.get_session(params):
                    return session_info

        # 创建新的会话
        return await self._create_session(params)

    async def _create_session(self, params: SessionCreateParams) -> PluginedSessionInfo:
        """
        创建新的浏览器会话（带并发保护）

        此方法只负责创建浏览器会话，不操作 LiveService 的状态。
        
        Returns:
            PluginedSessionInfo: 新创建的会话实例
        """
        start_time = time.time()

        # 🔑 第一阶段：获取或创建 BrowserSession
        if not (browser_session := self._active_sessions.get(params.mid)):
            browser_session = BrowserSession(mid=params.mid, sessions={})
            self._active_sessions[params.mid] = browser_session

        # 🔑 第二阶段：创建浏览器会话
        create_start = time.time()
        plugined_session = await browser_session.create_session(params)
        create_elapsed = time.time() - create_start
        logger.info(f"浏览器创建完成: mid={params.mid}, browser_id={params.browser_id}, 耗时: {create_elapsed:.3f}s")

        # 🔑 第三阶段：验证刚创建的浏览器是否仍然有效
        if plugined_session.is_closed:
            logger.warning(f"刚创建的浏览器已关闭: mid={params.mid}, browser_id={params.browser_id}")
            raise BrowserNotStartedException("浏览器在创建过程中被关闭，请重试")

        elapsed = time.time() - start_time
        logger.info(f"会话创建完成: mid={params.mid}, browser_id={params.browser_id}, 总耗时: {elapsed:.3f}s")

        return plugined_session

    async def release_all_session(self, mid: int) -> SessionAllCloseResponse:
        """
        释放指定mid的会话资源
        """
        async with self._pool_lock:
            if browser_session := self._active_sessions.get(mid):
                return await browser_session.remove_all_session()
        return SessionAllCloseResponse()

    async def release_session(self, params: BrowserSessionRemoveParams):
        if browser_session := self._active_sessions.get(params.mid):
            res = await browser_session.remove_session(params)
            if len(browser_session.sessions) == 0:
                del self._active_sessions[params.mid]
            return res

        return SessionCloseResponse(
            browser_id=params.browser_id,
            mid=params.mid,
            is_closed=False,
            feedback="会话不存在",
        )


# 全局单例实例
_default_session_pool: Optional[PlaywrightSessionPool] = None


def get_default_session_pool() -> PlaywrightSessionPool:
    """
    获取默认的会话池实例（单例）

    Returns:
        PlaywrightSessionPool实例
    """
    global _default_session_pool
    if _default_session_pool is None:
        _default_session_pool = PlaywrightSessionPool()
    return _default_session_pool


__all__ = ["get_default_session_pool"]
