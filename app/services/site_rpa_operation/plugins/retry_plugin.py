from typing import Callable

from app.models.RPA_browser.plugin_model import RetryPluginModel
from app.services.RPA_browser.notification_service import NotificationService
from app.services.site_rpa_operation.base.base_plugin import BasePlugin, PluginMethodType
import asyncio

from app.utils.depends.session_manager import DatabaseSessionManager


class RetryPlugin(BasePlugin):
    """重试插件 - 实现操作失败时的自动重试机制"""

    def __init__(self, conf: RetryPluginModel, **kwargs):
        super().__init__(**kwargs)
        self.conf = conf
        self.current_retry = 0
        self.original_operation = None
        self.retry_start_time = None

        self.logger.info(f"[RETRY PLUGIN] 🔄 重试插件初始化 - 最大重试次数: {conf.retry_times}, 延迟: {conf.delay}秒")
        self.logger.debug(
            f"[RETRY PLUGIN] ⚙️ 配置详情 - 错误推送: {conf.is_push_msg_on_error}, 插件描述: {conf.description}")

        # 添加操作到操作链
        self.add_operation(PluginMethodType.BEFORE_EXEC, self._setup_retry, "设置重试机制")
        self.add_operation(PluginMethodType.ON_ERROR, self._handle_retry, "处理重试逻辑")
        self.add_operation(PluginMethodType.ON_SUCCESS, self._reset_retry_count, "重置重试计数")

    async def _setup_retry(self):
        """设置重试机制"""
        import time
        self.current_retry = 0
        self.retry_start_time = time.time()
        self.logger.info(
            f"[RETRY PLUGIN] 🔄 初始化重试机制 - 最大重试次数: {self.conf.retry_times}, 延迟间隔: {self.conf.delay}秒")
        self.logger.debug(f"[RETRY PLUGIN] ⏰ 重试开始时间: {self.retry_start_time:.2f}")

    async def _handle_retry(self, error: Exception, operation: Callable | None = None, *args, **kwargs):
        """处理重试逻辑"""
        if operation:
            self.original_operation = operation
        if self.conf.is_push_msg_on_error:
            async with DatabaseSessionManager.async_session() as session:
                await NotificationService.push_msg(self.base_playwright_engine.browser_token, "重试失败",
                                                   f"第 {self.current_retry} 次重试失败\n{error}", session)
        if self.current_retry < self.conf.retry_times:
            self.current_retry += 1

            self.logger.warning(
                f"[RETRY PLUGIN] 第 {self.current_retry}/{self.conf.retry_times} 次重试，等待 {self.conf.delay} 秒后执行"
            )

            # 等待延迟时间
            await asyncio.sleep(self.conf.delay)

            # 执行重试
            if self.original_operation:
                try:
                    result = await self.original_operation(*args, **kwargs)
                    self.logger.info(f"[RETRY PLUGIN] 第 {self.current_retry} 次重试成功")
                    return result
                except Exception as e:
                    self.logger.error(f"[RETRY PLUGIN] 第 {self.current_retry} 次重试失败: {e}")

                    # 如果还有重试次数，继续重试
                    if self.current_retry < self.conf.retry_times:
                        return await self._handle_retry(error=e, *args, **kwargs)
                    else:
                        self.logger.error(f"[RETRY PLUGIN] 所有重试次数已用完")
                        raise e
            return None
        else:
            self.logger.error(f"[RETRY PLUGIN] 重试次数已用完，无法继续重试")
            return None

    async def _reset_retry_count(self):
        """重置重试计数"""
        import time
        if self.retry_start_time:
            total_retry_time = time.time() - self.retry_start_time
            self.logger.info(
                f"[RETRY PLUGIN] ✅ 操作成功 - 总重试时间: {total_retry_time:.3f}秒, 重试次数: {self.current_retry}")
        else:
            self.logger.info(f"[RETRY PLUGIN] ✅ 操作成功 - 重试次数: {self.current_retry}")

        self.current_retry = 0
        self.retry_start_time = None
        self.logger.debug("[RETRY PLUGIN] 🔄 重试计数已重置")


__all__ = ["RetryPlugin"]
