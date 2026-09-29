"""
浏览器会话依赖

`/browser/control/*` 下的接口几乎都需要同一段样板：
「校验浏览器归属 → 查会话表（不存在就报错）→ 刷新活跃时间 → 取 entry」。
此前每个接口各写一遍（错误码、touch 来源也各写一遍），本模块把它收敛成依赖。

前置条件不满足时统一**抛业务异常**，由统一异常处理器转成 `{code, msg}`：
- 与会话路由同口径，接口里不再手拼 `error_response`；
- 避免接口内的 `except Exception` 把业务异常吞成 500（见 docs/response-code-design.md）。
"""

from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Depends
from botright.playwright_mock import Page

from app.models.common.exceptions.base_exception import (
    BrowserNotStartedException,
    BrowserPageIndexError,
    BrowserWorkflowRunningException,
)
from app.models.runtime.live_service import BrowserSessionEntry
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    WebRTCEnabledSession,
)
from app.services.RPA_browser.session.live_service import LiveService, live_service
from bili_common.models.depends import BrowserReqAuthInfo
from app.utils.depends.security_depends import verify_browser_ownership


@dataclass
class ActiveBrowserSession:
    """已确认存在的活跃浏览器会话（依赖注入产物）。

    同时给出 `mid` / `browser_id`（拼响应、调 service 用）与 `entry`（取会话实体、
    生命周期状态用），接口因此不必再自己查会话表。
    """

    mid: int
    browser_id: int | str
    entry: BrowserSessionEntry

    @property
    def browser_session(self) -> WebRTCEnabledSession:
        return self.entry.browser_session

    @property
    def all_pages(self) -> list[Page]:
        return self.entry.browser_session.all_pages

    def get_page(self, page_index: int) -> Page:
        """按索引取页面；越界抛 `BrowserPageIndexError`（与 live_service 同口径）。

        Raises:
            BrowserPageIndexError: 索引为负数或超出当前页面数
        """
        pages = self.all_pages
        if page_index < 0 or page_index >= len(pages):
            raise BrowserPageIndexError(page_index)
        return pages[page_index]


def require_active_browser_session(
    source: str = "operation",
    *,
    reject_workflow_running: bool = False,
) -> Callable[..., Awaitable[ActiveBrowserSession]]:
    """构建「会话必须存在」的依赖。

    Args:
        source: `touch()` 的来源标记，仅用于日志定位「是哪个接口刷新的活跃时间」。
        reject_workflow_running: 工作流执行期拒绝本接口（执行期互斥，见计划书 §5.17）。
            判定用 `BrowserSessionEntry.workflow_run_id`：pin_count 区分不出
            「工作流在执行」与「用户自己在调试」。

    Returns:
        可交给 `Depends(...)` 使用的依赖函数。

    Raises:
        BrowserNotStartedException: 会话不存在（业务码 1007，HTTP 200 承载）
        BrowserWorkflowRunningException: 工作流执行中（业务码 2016，HTTP 200 承载）
    """

    async def _dependency(
        browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership),
    ) -> ActiveBrowserSession:
        mid = browser_req.auth_info.mid
        browser_id = browser_req.browser_id

        entry = LiveService._browser_sessions.get(
            LiveService._get_session_key(mid, browser_id)
        )
        if entry is None:
            raise BrowserNotStartedException()

        if reject_workflow_running and entry.is_workflow_running:
            raise BrowserWorkflowRunningException()

        # 真实操作入口才刷新活跃：解除闲置降级 / 取消「待关闭」宽限
        await live_service.touch(mid, browser_id, source=source)

        return ActiveBrowserSession(mid=mid, browser_id=browser_id, entry=entry)

    return _dependency


__all__ = [
    "ActiveBrowserSession",
    "require_active_browser_session",
]
