import asyncio
from typing import Dict, Optional
from app.models.RPA_browser.browser_session_model import (
    SessionCreateParams,
    BrowserSessionBaseParams,
    BrowserSessionRemoveParams,
    SessionAllCloseResponse,
    SessionCloseResponse,
)
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    BrowserSession,
    PluginedSessionInfo,
)


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
        创建新的浏览器会话
        Returns:
            (BaseUndetectedPlaywright实例, BrowserContext) 的元组
        """
        if browser_session := self._active_sessions.get(params.mid):
            return await browser_session.create_session(params)
        else:
            browser_session = BrowserSession(
                mid=params.mid, sessions={}
            )
            self._active_sessions[params.mid] = browser_session
            return await browser_session.create_session(params)

    async def release_all_session(
        self, params: BrowserSessionBaseParams
    ) -> SessionAllCloseResponse:
        """
        释放指定mid的会话资源

        """
        async with self._pool_lock:
            if browser_session := self._active_sessions.get(params.mid):
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
