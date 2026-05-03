import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, Any, Dict, List
import uuid
import loguru
from playwright.async_api import BrowserContext, Page
from app.models.runtime.session import (
    SessionCloseResponse,
    BrowserSessionCreateParams,
    BrowserSessionGetParams,
    BrowserSessionRemoveParams,
    SessionAllCloseResponse,
)
from app.models.runtime.api import (
    BrowserFingerprintQueryParams,
)
from app.models.core.browser.fingerprint import BaseFingerprintBrowserInitParams
from app.models.core.plugin.models import PluginBaseModel
from app.services.RPA_browser.base.base_engines import BaseUndetectedPlaywright
from app.services.site_rpa_operation.base.base_plugin import (
    BasePlugin,
    PluginMethodType,
)
from app.services.site_rpa_operation.plugins import PluginTypeEnum
from app.utils.decorator import log_class_decorator
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.RPA_browser.browser_db_service import BrowserDBService
from pydantic import computed_field
from app.config import settings
from app.services.RPA_browser.webrtc.stream_manager import WebRTCStreamManager

# 🔑 全局锁字典，用于保护浏览器创建过程（key: f"{mid}_{browser_id}"）
_browser_creation_locks: Dict[str, asyncio.Lock] = {}
_global_browser_lock = asyncio.Lock()  # 用于保护 _browser_creation_locks 字典本身

@dataclass
class InitSessionRes:
    playwright_instance: BaseUndetectedPlaywright
    browser_context: BrowserContext
    browser_generator: AsyncGenerator[BrowserContext, Any]
    fingerprint_params: BaseFingerprintBrowserInitParams


async def _get_browser_creation_lock(mid, browser_id) -> asyncio.Lock:
    """
    获取浏览器创建锁（懒创建）
    
    Args:
        mid: 用户ID
        browser_id: 浏览器ID
    
    Returns:
        asyncio.Lock: 针对该uid+browser_id的锁
    """
    lock_key = f"{mid}_{browser_id}"
    async with _global_browser_lock:
        if lock_key not in _browser_creation_locks:
            _browser_creation_locks[lock_key] = asyncio.Lock()
        return _browser_creation_locks[lock_key]


async def _cleanup_browser_creation_lock(mid, browser_id):
    """
    清理浏览器创建锁（在浏览器创建完成后调用）
    
    Args:
        mid: 用户ID
        browser_id: 浏览器ID
    """
    lock_key = f"{mid}_{browser_id}"
    async with _global_browser_lock:
        _browser_creation_locks.pop(lock_key, None)


@dataclass
@log_class_decorator.decorator
class SessionInfo:
    """
    会话信息数据类
    尽量不要直接实例化，通过`new`方法实例化
    """

    playwright_instance: BaseUndetectedPlaywright
    browser_context: BrowserContext
    browser_generator: AsyncGenerator[BrowserContext, Any]
    fingerprint_params: BaseFingerprintBrowserInitParams
    _headless: bool
    created_at: datetime = datetime.now()
    logger: "loguru.Logger" = None

    @computed_field
    @property
    def is_closed(self) -> bool:
        """检查会话是否已关闭"""
        if not self.browser_context:
            return True

        # 如果没有页面，认为是关闭状态
        if not self.browser_context.pages:
            return True

        # 检查第一个页面是否关闭
        try:
            return self.browser_context.pages[0].is_closed()
        except (AttributeError, IndexError):
            return True

    @computed_field
    @property
    def headless(self) -> bool:
        return self._headless
    
    @staticmethod
    def _ensure_page_id(page: Page) -> str:
        """
        确保 Page 对象有唯一的 page_id
        
        如果 Page 对象已经有 _webrtc_page_id 属性，则返回它；
        否则生成一个新的 UUID 并附加到 Page 对象上。
        
        Args:
            page: Playwright Page 对象
            
        Returns:
            str: 页面的唯一 ID
        """
        if not hasattr(page, '_webrtc_page_id'):
            page._webrtc_page_id = str(uuid.uuid4())
        return page._webrtc_page_id
    
    async def get_all_pages(self) -> list[Page]:
        """获取所有页面列表（自动为每个页面设置 page_id）"""
        if self.is_closed:
            return []

        # 返回所有未关闭的页面，并确保每个页面都有 page_id
        pages = []
        for page in self.browser_context.pages:
            if not page.is_closed():
                self._ensure_page_id(page)  # 确保有 page_id
                pages.append(page)
        return pages


@log_class_decorator.decorator
class PluginedSessionInfo(SessionInfo):
    page_methods_to_enhance: list[str] = [
        "click",
        "fill",
        "type",
        "press",
        "check",
        "uncheck",
        "select_option",
        "set_input_files",
        "focus",
        "blur",
        "drag_and_drop",
        "hover",
        "goto",
        "reload",
        "wait_for_selector",
        "wait_for_function",
        "evaluate",
        "evaluate_handle",
        "query_selector",
        "query_selector_all",
    ]
    
    # WebRTC 管理器（惰性初始化）
    _webrtc_manager = None
    
    @property
    def max_pages(self) -> int:
        """获取最大页面数限制"""
        return settings.browser_max_pages_per_context
    
    async def enable_webrtc(self):
        """
        动态启用 WebRTC 功能（惰性初始化）
        
        如果会话尚未启用 WebRTC，则创建 WebRTCStreamManager。
        如果已经启用，则直接返回现有的管理器。
        
        Returns:
            WebRTCStreamManager: WebRTC 流管理器实例
        """
        if self._webrtc_manager is None:
            self._webrtc_manager = WebRTCStreamManager(self)
        return self._webrtc_manager
    
    @property
    def has_webrtc(self) -> bool:
        """检查是否已启用 WebRTC 功能"""
        return self._webrtc_manager is not None
    
    @property
    def webrtc_manager(self):
        """
        获取 WebRTC 管理器
        
        如果尚未启用，会抛出异常。应该先调用 enable_webrtc()。
        """
        if self._webrtc_manager is None:
            raise RuntimeError(
                "WebRTC not enabled. Call await session.enable_webrtc() first."
            )
        return self._webrtc_manager
    
    @property
    def webrtc_active_streams(self) -> int:
        """获取活跃的 WebRTC 流数量"""
        if self._webrtc_manager:
            return self._webrtc_manager.active_stream_count
        return 0
    
    @property
    def webrtc_total_streams(self) -> int:
        """获取总的 WebRTC 流数量"""
        if self._webrtc_manager:
            return self._webrtc_manager.total_stream_count
        return 0

    async def close(self, page_index: int | None = None) -> SessionCloseResponse:
        """
        关闭会话或指定页面

        Args:
            page_index: 如果提供，则只关闭指定索引的页面；否则关闭整个会话

        Returns:
            SessionCloseResponse: 关闭响应
        """
        try:
            # 如果启用了 WebRTC，先关闭所有 WebRTC 流
            if self._webrtc_manager:
                await self._webrtc_manager.close_all_streams()
            
            # 如果指定了页面索引，只关闭该页面
            if page_index is not None:
                success = await self.close_page(page_index)
                if success:
                    return SessionCloseResponse(
                        mid=self.playwright_instance.mid,
                        browser_id=self.playwright_instance.browser_id,
                        is_closed=True,
                        feedback=f"页面 {page_index} 已关闭",
                    )
                else:
                    return SessionCloseResponse(
                        mid=self.playwright_instance.mid,
                        browser_id=self.playwright_instance.browser_id,
                        is_closed=False,
                        feedback=f"关闭页面 {page_index} 失败",
                    )

            # 否则关闭整个会话

            # 关闭浏览器上下文
            if self.browser_context and not (
                self.browser_context.pages and self.browser_context.pages[0].is_closed()
            ):
                await self.browser_context.close()

            # 关闭浏览器生成器
            if self.browser_generator:
                await self.browser_generator.aclose()

            return SessionCloseResponse(
                mid=self.playwright_instance.mid,
                browser_id=self.playwright_instance.browser_id,
                is_closed=True,
                feedback="会话已优雅关闭",
            )
        except Exception as e:
            self.logger.error(f"关闭会话时出错: {e}")
            return SessionCloseResponse(
                mid=self.playwright_instance.mid,
                browser_id=self.playwright_instance.browser_id,
                is_closed=False,
                feedback=f"关闭会话时出错: {str(e)}",
            )

    async def force_close(self) -> SessionCloseResponse:
        """强制关闭会话"""
        try:
            # 强制关闭所有 WebRTC 流
            if self._webrtc_manager:
                await self._webrtc_manager.close_all_streams()
            
            # 强制关闭浏览器上下文
            if self.browser_context:
                try:
                    await self.browser_context.close()
                except Exception as e:
                    self.logger.error(f"强制关闭浏览器上下文时出错: {e}")

            # 关闭浏览器生成器
            if self.browser_generator:
                try:
                    await self.browser_generator.aclose()
                except Exception as e:
                    self.logger.error(f"关闭浏览器生成器时出错: {e}")

            return SessionCloseResponse(
                mid=self.playwright_instance.mid,
                browser_id=self.playwright_instance.browser_id,
                is_closed=True,
                feedback="会话已强制关闭",
            )
        except Exception as e:
            self.logger.error(f"强制关闭会话时出错: {e}")
            return SessionCloseResponse(
                mid=self.playwright_instance.mid,
                browser_id=self.playwright_instance.browser_id,
                is_closed=False,
                feedback=f"强制关闭会话时出错: {str(e)}",
            )

    async def __new_page(self) -> Page:
        """创建新页面并自动注入插件"""
        # 检查页面数量限制
        await self._enforce_page_limit()
        
        page = await self.browser_context.new_page()
        # 为新页面设置 page_id
        self._ensure_page_id(page)
        # 禁用下载功能
        self._disable_downloads(page)
        self.logger.info(f"📄 创建新页面，当前页面总数: {len(self.browser_context.pages)}/{self.max_pages}")
        return page

    def _disable_downloads(self, page: Page) -> None:
        """
        禁用页面下载功能

        通过拦截所有下载事件来阻止文件下载
        """

        async def on_download(download):
            """下载事件处理器 - 取消所有下载"""
            try:
                # 取消下载
                await download.cancel()
                self.logger.warning(f"已阻止下载: {download.url}")
            except Exception as e:
                self.logger.error(f"取消下载失败: {e}")

        # 监听下载事件
        page.on("download", on_download)

    async def _enforce_page_limit(self):
        """
        强制执行页面数量限制
        如果页面数量超过限制，关闭最旧的页面
        """
        all_pages = await self.get_all_pages()
        current_count = len(all_pages)
        
        if current_count >= self.max_pages:
            pages_to_close = current_count - self.max_pages + 1
            self.logger.warning(
                f"⚠️ 页面数量达到限制 ({current_count}/{self.max_pages})，将关闭 {pages_to_close} 个最旧的页面"
            )
            
            # 关闭最旧的页面（前面的页面）
            for i in range(pages_to_close):
                if i < len(all_pages):
                    old_page = all_pages[i]
                    try:
                        if not old_page.is_closed():
                            await old_page.close()
                            self.logger.info(f"🗑️ 已关闭旧页面: {old_page.url}")
                            
                    except Exception as e:
                        self.logger.error(f"关闭旧页面失败: {e}")
            
            # 等待一下让浏览器完成清理
            await asyncio.sleep(0.5)
    
    async def create_new_page_with_limit(self) -> Page:
        """
        创建新页面并遵守页面数量限制
        这是推荐的使用方式，会自动执行页面限制检查
        
        Returns:
            Page: 新创建的页面对象
        """
        return await self.__new_page()

    async def switch_to_page(self, page_index: int) -> Page:
        """
        切换到指定索引的页面

        Args:
            page_index: 页面索引（从0开始）

        Returns:
            Page: 切换后的页面对象

        Raises:
            IndexError: 如果索引超出范围
        """
        if self.is_closed:
            raise RuntimeError("会话已关闭，无法切换页面")

        all_pages = await self.get_all_pages()

        if not all_pages:
            raise IndexError("没有可用的页面")

        if page_index < 0 or page_index >= len(all_pages):
            raise IndexError(
                f"页面索引 {page_index} 超出范围，可用范围: 0-{len(all_pages)-1}"
            )

        target_page = all_pages[page_index]
        
        # 使用 bring_to_front() 真正激活页面
        try:
            await target_page.bring_to_front()
            self.logger.info(f"✅ 已激活页面索引 {page_index}: {target_page.url}")
        except Exception as e:
            self.logger.warning(f"⚠️ bring_to_front() 失败: {e}，但仍返回页面对象")
        
        return target_page

    async def close_page(self, page_index: int) -> bool:
        """
        关闭指定索引的页面

        Args:
            page_index: 页面索引（从0开始）

        Returns:
            bool: 是否成功关闭
        """
        if self.is_closed:
            raise RuntimeError("会话已关闭，无法关闭页面")

        all_pages = await self.get_all_pages()

        if not all_pages:
            raise IndexError("没有可用的页面")

        if page_index < 0 or page_index >= len(all_pages):
            raise IndexError(
                f"页面索引 {page_index} 超出范围，可用范围: 0-{len(all_pages)-1}"
            )

        target_page = all_pages[page_index]

        try:
            await target_page.close()

            self.logger.info(f"已关闭页面索引 {page_index}")
            return True
        except Exception as e:
            self.logger.error(f"关闭页面失败: {e}")
            return False

    async def get_current_page(self) -> Page:
        """获取当前活动页面并确保已注入插件"""
        all_pages = self.browser_context.pages if self.browser_context else []
        
        if not all_pages:
            # 没有页面，创建新页面
            return await self.__new_page()
        
        # 查找第一个未关闭且可用的页面
        for page in all_pages:
            if not page.is_closed():
                self._ensure_page_id(page)  # 确保有 page_id
                return page
        
        # 所有页面都关闭了，创建新页面
        return await self.__new_page()

    async def _get_page(self):
        if self.is_closed:
            return await self._create_session()
        return self.browser_context.pages[0]

    @classmethod
    async def _initialize_session(
        cls, mid, browser_id, headless=False
    ) -> InitSessionRes:
        """初始化会话的公共方法（带锁保护，防止并发创建）"""
        # 🔑 获取针对 uid+browser_id 的锁
        lock = await _get_browser_creation_lock(mid, browser_id)
        async with lock:
            try:
                # 获取浏览器指纹信息
                async with DatabaseSessionManager.async_session() as session:
                    fingerprint_info = await BrowserDBService.read_fingerprint(
                        params=BrowserFingerprintQueryParams(id=browser_id),
                        mid=mid,
                        session=session,
                    )

                if not fingerprint_info:
                    raise ValueError("浏览器指纹信息不存在")

                fingerprint_params = BaseFingerprintBrowserInitParams(
                    **fingerprint_info.model_dump(exclude_none=True)
                )

                playwright_instance = BaseUndetectedPlaywright(
                    mid=mid, browser_id=browser_id, headless=headless
                )
                browser_generator = playwright_instance.launch_browser_span(fingerprint_params)
                browser_context = await anext(browser_generator)

                return InitSessionRes(
                    **{
                        "playwright_instance": playwright_instance,
                        "browser_context": browser_context,
                        "browser_generator": browser_generator,
                        "fingerprint_params": fingerprint_params,
                    }
                )
            finally:
                # 🔑 清理锁（可选，避免内存泄漏）
                await _cleanup_browser_creation_lock(mid, browser_id)

    async def _create_session(self):
        """创建会话实例"""
        init_data = await self._initialize_session(
            mid=self.playwright_instance.mid,
            browser_id=self.playwright_instance.browser_id,
            headless=self.headless,
        )

        self.playwright_instance = init_data.playwright_instance
        self.browser_context = init_data.browser_context
        self.browser_generator = init_data.browser_generator
        self.fingerprint_params = init_data.fingerprint_params

    @classmethod
    async def new(cls, mid, browser_id, headless=False):
        """
        创建新的浏览器实例，并且自动查询插件配置进行初始化
        """
        init_data = await cls._initialize_session(mid, browser_id, headless)

        return cls(
            playwright_instance=init_data.playwright_instance,
            browser_context=init_data.browser_context,
            browser_generator=init_data.browser_generator,
            fingerprint_params=init_data.fingerprint_params,
            _headless=headless,
        )


@dataclass
class BrowserSession:
    """浏览器会话数据类，包含特定mid下的所有browser_id会话"""

    mid: str
    sessions: Dict[int, PluginedSessionInfo]
    created_at: datetime = datetime.now()

    async def create_session(
        self, params: BrowserSessionCreateParams
    ) -> PluginedSessionInfo:
        """添加新的会话"""
        if sess := self.get_session(params):
            return sess
        session_info: PluginedSessionInfo = await PluginedSessionInfo.new(
            mid=self.mid,
            browser_id=params.browser_id,
            headless=params.headless,
        )
        # 注册插件
        self.sessions[params.browser_id] = session_info
        return session_info

    def get_session(
        self, params: BrowserSessionGetParams
    ) -> PluginedSessionInfo | None:
        """根据browser_id获取会话"""
        if session_info := self.sessions.get(params.browser_id):
            session_info.last_used_at = int(time.time())
            return session_info
        return None

    async def remove_session(
        self, params: BrowserSessionRemoveParams
    ) -> SessionCloseResponse:
        """移除指定browser_id的会话"""
        if params.browser_id in self.sessions:
            if params.force_close:
                res = await self.sessions[params.browser_id].force_close()
                del self.sessions[params.browser_id]
                return res
            else:
                res = await self.sessions[params.browser_id].close()
                return res
        return SessionCloseResponse(
            browser_id=params.browser_id,
            mid=self.mid,
            is_closed=False,
            feedback="会话不存在",
        )

    async def remove_all_session(
        self, force_close: bool = False
    ) -> SessionAllCloseResponse:
        """移除所有会话"""
        res = SessionAllCloseResponse()
        for browser_id in list(self.sessions.keys()):
            if force_close:
                resp = await self.sessions[browser_id].force_close()
                del self.sessions[browser_id]
            else:
                resp = await self.sessions[browser_id].close()
            res.items.append(resp)
        return res


__all__ = [
    "SessionInfo",
    "PluginedSessionInfo",
    "BrowserSession",
]
