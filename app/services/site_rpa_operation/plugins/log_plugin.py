import os
import time
from urllib.parse import urlparse
from pydantic import computed_field

from app.models.core.plugin.models import LogPluginModel
from app.services.site_rpa_operation.base.base_plugin import BasePlugin, PluginMethodType
from app.config import CONF


class LogPlugin(BasePlugin):
    """日志插件 - 提供详细的执行日志记录"""

    def __init__(self, conf: LogPluginModel, **kwargs):
        super().__init__(**kwargs)
        self.conf = conf
        self.operation_start_time = None
        self.logger.info(f"[LOG PLUGIN] 📝 日志插件初始化 - 日志级别: {conf.log_level}")
        # 向各个生命周期方法添加日志操作
        self._setup_log_operations()
    @computed_field
    @property
    def screenshot_path(self):
        return os.path.join(CONF.Path.logs, "screenshots", str(self.conf.browser_token), str(self.conf.browser_info_id))

    def _setup_log_operations(self):
        """设置日志操作链"""
        # before_exec 操作链
        self.add_operation(PluginMethodType.BEFORE_EXEC, self._log_start_operation, "记录操作开始")
        self.add_operation(PluginMethodType.BEFORE_EXEC, self._log_operation_context, "记录操作上下文")

        # after_exec 操作链  
        self.add_operation(PluginMethodType.AFTER_EXEC, self._log_operation_complete, "记录操作完成")
        self.add_operation(PluginMethodType.AFTER_EXEC, self._log_execution_time, "记录执行时间")

        # on_exec 操作链
        self.add_operation(PluginMethodType.ON_EXEC, self._log_operation_progress, "记录操作进度")

        # on_error 操作链
        self.add_operation(PluginMethodType.ON_ERROR, self._log_error_details, "记录错误详情")
        self.add_operation(PluginMethodType.ON_ERROR, self._log_error_context, "记录错误上下文")

        # on_success 操作链
        self.add_operation(PluginMethodType.ON_SUCCESS, self._log_success_details, "记录成功详情")
        self.add_operation(PluginMethodType.ON_SUCCESS, self._log_result_summary, "记录结果摘要")

    async def _log_start_operation(self):
        """记录操作开始"""
        import time
        self.operation_start_time = time.time()
        self.logger.info(f"[LOG PLUGIN] 🚀 开始执行操作 - 时间戳: {self.operation_start_time:.2f}")
        self.logger.debug(
            f"[LOG PLUGIN] 📋 配置信息 - 日志级别: {self.conf.log_level}, 插件描述: {self.conf.description}")

    async def _log_operation_context(self):
        """记录操作上下文"""
        self.logger.debug(f"[LOG PLUGIN] 📋 操作上下文 - 浏览器引擎: {type(self.base_playwright_engine).__name__}")
        self.logger.debug(f"[LOG PLUGIN] 📋 操作上下文 - 会话状态: {'已连接' if self.session else '未连接'}")

    async def _log_operation_complete(self):
        """记录操作完成"""
        self.logger.info("[LOG PLUGIN] ✅ 操作执行完成")

    async def _log_execution_time(self):
        """记录执行时间"""
        import time
        if self.operation_start_time:
            execution_time = time.time() - self.operation_start_time
            self.logger.info(f"[LOG PLUGIN] ⏱️ 操作执行时间: {execution_time:.3f}秒")
            if execution_time > 5.0:
                self.logger.warning(f"[LOG PLUGIN] ⚠️ 操作执行时间较长: {execution_time:.3f}秒")
        else:
            self.logger.debug("[LOG PLUGIN] ⏱️ 无法计算执行时间（缺少开始时间）")

    async def _log_operation_progress(self):
        """记录操作进度"""
        self.logger.debug("[LOG PLUGIN] 📊 操作进行中...")

    async def _log_error_details(self, error: Exception = None):
        """记录错误详情"""
        if error:
            self.logger.error(f"[LOG PLUGIN] ❌ 操作执行出错: {error}")
        else:
            self.logger.error("[LOG PLUGIN] ❌ 操作执行出错")

    async def _log_error_context(self, error: Exception = None):
        """记录错误上下文"""
        self.logger.debug(f"[LOG PLUGIN] 🔍 错误上下文 - 重试次数: 待实现")
        if error:
            self.logger.debug(f"[LOG PLUGIN] 🔍 错误类型: {type(error).__name__}")

    async def _log_success_details(self):
        """记录成功详情"""
        self.logger.info("[LOG PLUGIN] 🎉 操作执行成功")

    async def _log_result_summary(self):
        """记录结果摘要"""
        self.logger.debug("[LOG PLUGIN] 📈 结果摘要: 操作顺利完成")


__all__ = ["LogPlugin"]
