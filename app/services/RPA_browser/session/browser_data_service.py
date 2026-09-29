"""
浏览器运行态数据服务

只处理「会话正在跑、不需要重启浏览器就能复位」的数据 —— 目前是登录态：
cookie（含 HttpOnly）+ 权限授权 + 页面 web storage。

⚠️ 刻意不碰 profile 里的磁盘数据（HTTP 缓存 / Service Worker / IndexedDB / 扩展数据）：
清那些必须删掉 `user_data_dir` 再重建浏览器，属于另一条路径，不在本服务范围内。
反之，若站点把凭证存在 IndexedDB / SW 里（不靠 cookie），本服务对它无效。
"""

from loguru import logger

from botright.playwright_mock import BrowserContext, Page

from app.models.runtime.browser_operation import ClearLoginStateResponse
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    WebRTCEnabledSession,
)


class BrowserDataService:
    """浏览器运行态数据（登录态等）清理服务。"""

    #: 清 web storage 的脚本。about:blank / sandbox 页面访问 localStorage 会抛异常，
    #: 故脚本内部再兜一层，避免「这个页面没有 storage」把整页清理打断。
    _CLEAR_WEB_STORAGE_SCRIPT = (
        "() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"
    )

    @staticmethod
    async def count_cookie_domains(browser_context: BrowserContext) -> int:
        """统计 cookie 覆盖的站点数（只读）。

        供前端在二次确认时提示「会退出 N 个站点的登录态」，必须在清理**之前**调用。
        """
        cookies = await browser_context.cookies()
        return len({cookie["domain"] for cookie in cookies})

    @classmethod
    async def clear_login_state(
        cls,
        browser_session: WebRTCEnabledSession,
        *,
        reload_pages: bool = True,
    ) -> ClearLoginStateResponse:
        """清空登录态：cookie + 权限 + 页面 web storage，可选刷新页面。

        顺序：先 cookie / 权限（登录态的真正载体），再逐页清 web storage，
        最后按需刷新 —— 刷新放在最后，站点重新加载时看到的就是未登录态。

        Args:
            browser_session: 活跃会话实体（由调用方保证会话存在、活跃时间已刷新）
            reload_pages: 是否刷新已打开页面。不刷新的话页面上仍是旧账号的 DOM，
                用户会以为操作没生效

        Returns:
            ClearLoginStateResponse: 受影响站点数 / 已清理页面数 / 已刷新页面数
        """
        browser_context = browser_session.browser_context

        # 先数再清：清完就没法统计影响范围了
        affected_domains = await cls.count_cookie_domains(browser_context)

        await browser_context.clear_cookies()
        await browser_context.clear_permissions()

        cleared_pages = 0
        reloaded_pages = 0
        for page in browser_session.all_pages:
            if await cls._clear_page_web_storage(page):
                cleared_pages += 1
            if reload_pages and await cls._reload_page(page):
                reloaded_pages += 1

        return ClearLoginStateResponse(
            cleared_cookies=True,
            cleared_pages=cleared_pages,
            reloaded_pages=reloaded_pages,
            affected_domains=affected_domains,
            message=f"已退出登录（影响 {affected_domains} 个站点），浏览器未重启，可登录新账号",
        )

    @classmethod
    async def _clear_page_web_storage(cls, page: Page) -> bool:
        """清单个页面的 localStorage / sessionStorage。

        单页失败（about:blank / sandbox）只跳过该页，不能让整次清理失败。
        """
        try:
            await page.evaluate(cls._CLEAR_WEB_STORAGE_SCRIPT)
            return True
        except Exception as e:
            logger.debug(f"清理页面 web storage 跳过: {e}")
            return False

    @staticmethod
    async def _reload_page(page: Page) -> bool:
        """刷新单个页面让站点回到未登录态。

        失败只告警：cookie 已清空才是本次操作的目的，刷新失败不影响「已登出」这个结果。
        """
        try:
            await page.reload()
            return True
        except Exception as e:
            logger.warning(f"清空登录态后刷新页面失败: {e}")
            return False


__all__ = ["BrowserDataService"]
