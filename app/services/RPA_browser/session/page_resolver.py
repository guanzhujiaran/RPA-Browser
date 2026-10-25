"""浏览器页面解析（控制路由与调试服务共用）。

从 ``execution_router._resolve_page`` 下沉而来，避免 MCP 调试服务反向依赖 controller 层。
"""

from __future__ import annotations

from botright.playwright_mock.page import Page

from app.models.common.exceptions.base_exception import (
    BrowserWorkflowRunningException,
)
from app.services.RPA_browser.session.live_service import live_service


async def resolve_page(
    mid: int,
    browser_id: int | str,
    page_index: int | None = None,
) -> Page:
    """取目标浏览器页面。

    会话不存在时由 ``get_browser_session_entry`` 抛 ``BrowserNotStartedException``（code=1007），
    表达「需要先启动浏览器」；工作流执行期间拒绝调试类调用（执行期互斥）。
    """
    entry = live_service.get_browser_session_entry(mid=mid, browser_id=browser_id)
    if entry.is_workflow_running:
        raise BrowserWorkflowRunningException()

    if page_index is None:
        return await entry.browser_session.get_current_page()

    all_pages = entry.browser_session.all_pages
    if not all_pages:
        raise ValueError(f"浏览器 {browser_id} 没有打开任何页面")
    if not (0 <= page_index < len(all_pages)):
        raise ValueError(f"页面索引 {page_index} 超出范围 (0-{len(all_pages) - 1})")
    return all_pages[page_index]


__all__ = ["resolve_page"]
