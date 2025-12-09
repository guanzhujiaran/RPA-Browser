import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, Any, Dict, List
import uuid
import loguru
from playwright.async_api import BrowserContext, Page
from app.models.RPA_browser.browser_session_model import SessionCloseResponse, BrowserSessionCreateParams, \
    BrowserSessionGetParams, BrowserSessionRemoveParams, SessionAllCloseResponse
from app.models.RPA_browser.browser_info_model import UserBrowserInfoReadParams, \
    BaseFingerprintBrowserInitParams
from app.models.RPA_browser.plugin_model import PluginBaseModel
from app.services.RPA_browser.base.base_engines import BaseUndetectedPlaywright
from app.services.RPA_browser.plugin_db_service import PluginDBService
from app.services.site_rpa_operation.base.base_plugin import BasePlugin, PluginMethodType
from app.services.site_rpa_operation.plugins import PluginTypeEnum
from app.utils.decorator import log_class_decorator
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.RPA_browser.browser_db_service import BrowserDBService

create_session_lock = asyncio.Lock()


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
    _headless: bool
    created_at: datetime = datetime.now()
    logger: "loguru.Logger" = None

    @property
    def is_closed(self) -> bool:
        return self.browser_context and self.browser_context.pages and self.browser_context.pages[0].is_closed()

    @property
    def headless(self) -> bool:
        return self._headless


@log_class_decorator.decorator
class PluginedSessionInfo(SessionInfo):
    plugin_configs: Dict[PluginTypeEnum, PluginBaseModel] = None  # 插件配置
    plugin_instances: List[BasePlugin] = None  # 插件实例列表
    _enhanced_pages: set = set()  # 存储已增强的页面对象

    def reg_plugins(self):
        """注册插件，将插件实例化，但是还没有注入到页面"""
        if not self.plugin_configs:
            self.logger.info("[PLUGIN MANAGER] 📦 没有配置插件")
            return

        self.logger.info(f"[PLUGIN MANAGER] 🚀 开始注册插件 - 总配置数: {len(self.plugin_configs)}")

        # 实例化插件并设置共享资源
        self.plugin_instances = []
        enabled_count = 0

        for plugin_type, plugin_config in self.plugin_configs.items():
            # 获取插件类
            plugin_class = plugin_type.str_2_class
            if plugin_class and plugin_config.is_enabled:
                try:
                    self.logger.debug(f"[PLUGIN MANAGER] 🔧 正在注册插件: {plugin_type.value} - {plugin_config.name}")
                    plugin = plugin_class(
                        base_playwright_engine=self.playwright_instance,
                        session=self.browser_context,
                        logger=self.logger,
                        conf=plugin_config
                    )
                    self.plugin_instances.append(plugin)
                    enabled_count += 1
                    self.logger.info(f"[PLUGIN MANAGER] ✅ 插件注册成功: {plugin_type.value} - {plugin_config.name}")
                except Exception as e:
                    self.logger.error(
                        f"[PLUGIN MANAGER] ❌ 插件注册失败: {plugin_type.value} - {plugin_config.name}, 错误: {e}")
            else:
                if not plugin_config.is_enabled:
                    self.logger.debug(f"[PLUGIN MANAGER] ⏸️ 插件已禁用: {plugin_type.value} - {plugin_config.name}")
                else:
                    self.logger.warning(f"[PLUGIN MANAGER] ⚠️ 插件类未找到: {plugin_type.value}")

        self.logger.info(f"[PLUGIN MANAGER] 📊 插件注册完成 - 启用: {enabled_count}/{len(self.plugin_configs)}")

    async def close(self) -> SessionCloseResponse:
        """优雅关闭会话"""
        try:
            # 执行插件的清理操作
            if self.plugin_instances:
                for plugin in self.plugin_instances:
                    if hasattr(plugin, 'cleanup'):
                        try:
                            await plugin.cleanup()
                        except Exception as e:
                            self.logger.error(f"插件 {plugin.__class__.__name__} 清理时出错: {e}")

            # 关闭浏览器上下文
            if self.browser_context:
                # 检查是否有页面且页面未关闭
                if not (self.browser_context.pages and self.browser_context.pages[0].is_closed()):
                    await self.browser_context.close()

            # 关闭浏览器生成器
            if self.browser_generator:
                await self.browser_generator.aclose()

            # 清理增强页面集合，防止内存泄漏
            self._enhanced_pages.clear()

            return SessionCloseResponse(
                browser_token=self.playwright_instance.browser_token,
                browser_id=self.playwright_instance.browser_id,
                is_closed=True,
                feedback="会话已优雅关闭"
            )
        except Exception as e:
            self.logger.error(f"关闭会话时出错: {e}")
            return SessionCloseResponse(
                browser_token=self.playwright_instance.browser_token,
                browser_id=self.playwright_instance.browser_id,
                is_closed=False,
                feedback=f"关闭会话时出错: {str(e)}"
            )

    async def force_close(self) -> SessionCloseResponse:
        """强制关闭会话"""
        try:
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

            # 清理增强页面集合，防止内存泄漏
            if self._enhanced_pages is not None:
                self._enhanced_pages.clear()

            return SessionCloseResponse(
                browser_token=self.playwright_instance.browser_token,
                browser_id=self.playwright_instance.browser_id,
                is_closed=True,
                feedback="会话已强制关闭"
            )
        except Exception as e:
            self.logger.error(f"强制关闭会话时出错: {e}")
            return SessionCloseResponse(
                browser_token=self.playwright_instance.browser_token,
                browser_id=self.playwright_instance.browser_id,
                is_closed=False,
                feedback=f"强制关闭会话时出错: {str(e)}"
            )

    async def __execute_plugins(self, method_name: PluginMethodType, *args, **kwargs):
        """执行所有插件的指定方法"""
        if self.plugin_instances:
            for plugin in self.plugin_instances:
                method = getattr(plugin, method_name, None)
                if method:
                    try:
                        await method(*args, **kwargs)
                    except Exception as e:
                        self.logger.error(f"插件 {plugin.__class__.__name__} 执行 {method_name} 时出错: {e}")

    async def __execute_with_plugins(self, operation_func, *args, **kwargs):
        """使用插件执行操作"""
        try:
            # 执行 before_exec 钩子
            await self.__execute_plugins(PluginMethodType.BEFORE_EXEC)
            # 执行 on_exec 钩子
            await self.__execute_plugins(PluginMethodType.ON_EXEC)

            # 执行实际操作
            result = await operation_func(*args, **kwargs)

            # 执行 on_success 钩子
            await self.__execute_plugins(PluginMethodType.ON_SUCCESS)

            return result
        except Exception as e:
            # 执行 on_error 钩子
            await self.__execute_plugins(PluginMethodType.ON_ERROR, e)
            # 重新抛出异常
            raise
        finally:
            # 执行 after_exec 钩子
            await self.__execute_plugins(PluginMethodType.AFTER_EXEC)

    def __enhance_page_method(self, page: Page, method_name: str) -> None:
        """增强页面对象的指定方法"""
        original_method = getattr(page, method_name)

        async def enhanced_method(*args, **kwargs):
            # 创建操作函数
            async def operation():
                return await original_method(*args, **kwargs)

            # 使用插件执行操作
            return await self.__execute_with_plugins(operation)

        # 替换原始方法
        setattr(page, method_name, enhanced_method)


    def __inject_plugins_to_page(self, page: Page) -> Page:
        """将插件注入到页面对象中，增强其方法"""
        if not self.plugin_instances or (self._enhanced_pages is not None and id(page) in self._enhanced_pages):
            return page
        # 需要增强的页面方法列表
        page_methods_to_enhance = [
            'click', 'fill', 'type', 'press', 'check', 'uncheck', 'select_option',
            'set_input_files', 'focus', 'blur', 'drag_and_drop', 'hover',
            'goto', 'reload', 'wait_for_selector', 'wait_for_function',
            'evaluate', 'evaluate_handle', 'query_selector', 'query_selector_all'
        ]

        for method_name in page_methods_to_enhance:
            if hasattr(page, method_name) and callable(getattr(page, method_name)):
                self.__enhance_page_method(page, method_name)

        # 标记页面已增强
        enabled_plugins = [f"{v.name}: {v.description}" for k, v in self.plugin_configs.items() if v.is_enabled]
        plugin_count = len(enabled_plugins)
        self._enhanced_pages.add(id(page))
        self.logger.info(f"[PLUGIN INJECTION] 🎯 页面 {id(page)} 插件注入完成 - 活跃插件数: {plugin_count}")
        self.logger.debug(f"[PLUGIN INJECTION] 📋 已注入插件列表:\n" + "\n".join(enabled_plugins))
        self.logger.debug(
            f"[PLUGIN INJECTION] 📊 总增强页面数: {len(self._enhanced_pages) if self._enhanced_pages else 0}"
            f"\n{
            '\n'.join([
                f"{v.name}: {v.description}" for k, v in self.plugin_configs.items() if v.is_enabled
            ])
            }")
        return page

    async def __new_page(self) -> Page:
        """创建新页面并自动注入插件"""
        page = await self.browser_context.new_page()
        return self.__inject_plugins_to_page(page)

    async def get_current_page(self) -> Page:
        """获取当前活动页面并确保已注入插件"""
        # BrowserContext有pages属性，返回所有页面列表
        if current_page := await self._get_page():
            return self.__inject_plugins_to_page(current_page)

        self.logger.warning("没有找到当前活动页面，尝试创建页面")
        return await self.__new_page()

    async def _get_page(self):
        if self.is_closed:
            return await self._create_session()
        return self.browser_context.pages[0]

    @classmethod
    async def _initialize_session(cls, browser_token, browser_id, headless=True):
        """初始化会话的公共方法"""
        # 获取浏览器指纹信息
        async with DatabaseSessionManager.async_session() as session:
            fingerprint_info = await BrowserDBService.read_fingerprint(
                params=UserBrowserInfoReadParams(
                    browser_token=browser_token,
                    id=browser_id
                ),
                session=session
            )

            plugin_configs = await PluginDBService.get_browser_info_plugins(
                browser_token=browser_token,
                browser_id=browser_id,
                session=session
            )

        if not fingerprint_info:
            raise ValueError("浏览器指纹信息不存在")

        fingerprint_params = BaseFingerprintBrowserInitParams(
            **fingerprint_info.model_dump(exclude_none=True)
        )

        playwright_instance = BaseUndetectedPlaywright(
            browser_token=browser_token,
            browser_id=browser_id,
            headless=headless
        )
        browser_generator = playwright_instance.launch_browser_span(fingerprint_params)
        browser_context = await anext(browser_generator)

        return {
            'playwright_instance': playwright_instance,
            'browser_context': browser_context,
            'browser_generator': browser_generator,
            'plugin_configs': plugin_configs,
            'fingerprint_params': fingerprint_params
        }

    async def _create_session(self):
        """创建会话实例"""
        init_data = await self._initialize_session(
            browser_token=self.playwright_instance.browser_token,
            browser_id=self.playwright_instance.browser_id,
            headless=self.headless
        )

        self.playwright_instance = init_data['playwright_instance']
        self.browser_context = init_data['browser_context']
        self.browser_generator = init_data['browser_generator']

    @classmethod
    async def new(cls, browser_token, browser_id, headless=True):
        """
        创建新的浏览器实例，并且自动查询插件配置进行初始化
        """
        init_data = await cls._initialize_session(browser_token, browser_id, headless)

        result = cls(
            playwright_instance=init_data['playwright_instance'],
            browser_context=init_data['browser_context'],
            browser_generator=init_data['browser_generator'],
            _headless=headless
        )
        result.plugin_configs = init_data['plugin_configs']
        result.reg_plugins()

        return result


@dataclass
class BrowserSession:
    """浏览器会话数据类，包含特定browser_token下的所有browser_id会话"""
    browser_token: uuid.UUID
    sessions: Dict[int, PluginedSessionInfo]
    created_at: datetime = datetime.now()

    async def create_session(self, params: BrowserSessionCreateParams) -> PluginedSessionInfo:
        """添加新的会话"""
        async with create_session_lock:
            if sess := self.get_session(params):
                return sess
            session_info: PluginedSessionInfo = await PluginedSessionInfo.new(
                browser_token=self.browser_token,
                browser_id=params.browser_id,
                headless=params.headless
            )
            # 注册插件
            self.sessions[params.browser_id] = session_info
            return session_info

    def get_session(self, params: BrowserSessionGetParams) -> PluginedSessionInfo | None:
        """根据browser_id获取会话"""
        if session_info := self.sessions.get(params.browser_id):
            session_info.last_used_at = int(time.time())
            return session_info
        return None

    async def remove_session(self, params: BrowserSessionRemoveParams) -> SessionCloseResponse:
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
            browser_token=self.browser_token,
            is_closed=False,
            feedback="会话不存在"
        )

    async def remove_all_session(self, force_close: bool = False) -> SessionAllCloseResponse:
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
