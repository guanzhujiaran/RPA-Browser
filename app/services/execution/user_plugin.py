"""
用户自定义插件系统

核心设计:
1. 用户定义的插件通过继承 BaseCustomPlugin 基类实现
2. 插件注册到 PluginRegistry 中
3. 插件可以在操作执行前、后、错误时执行自定义逻辑
4. 支持依赖注入：可以访问 session、page、browser 等对象
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type
from enum import Enum


# 兼容 Python 3.10 的 StrEnum
class StrEnum(str, Enum):
    """字符串枚举，兼容 Python 3.10"""
    def __str__(self):
        return str(self.value)


import asyncio
import loguru

from app.models.RPA_browser.plugin_model import PluginBaseModel


class PluginHookType(StrEnum):
    """插件钩子类型"""
    BEFORE_ACTION = "before_action"      # 操作执行前
    AFTER_ACTION = "after_action"         # 操作执行后
    ON_ERROR = "on_error"                # 操作出错时
    ON_TIMEOUT = "on_timeout"             # 操作超时时
    ON_SUCCESS = "on_success"            # 操作成功时


@dataclass
class PluginContext:
    """插件执行上下文"""
    session_id: str
    browser_id: str
    action_name: str
    action_params: Dict[str, Any]
    user_data: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[Exception] = None
    execution_time: float = 0.0


@dataclass
class PluginMetadata:
    """插件元数据"""
    id: str
    name: str
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    hooks: List[PluginHookType] = field(default_factory=list)
    priority: int = 100  # 优先级，越小越先执行
    config_schema: Optional[Dict[str, Any]] = None


class BaseCustomPlugin(ABC):
    """
    用户自定义插件基类

    用户需要:
    1. 继承此类
    2. 实现 get_metadata() 方法定义插件元数据
    3. 实现需要的钩子方法 (before_action, after_action 等)
    4. 在 on_init() 中注册到 PluginRegistry

    示例:
    ```python
    class MyPlugin(BaseCustomPlugin):
        def get_metadata(self) -> PluginMetadata:
            return PluginMetadata(
                id="my_plugin",
                name="我的插件",
                hooks=[PluginHookType.BEFORE_ACTION, PluginHookType.AFTER_ACTION]
            )

        async def before_action(self, ctx: PluginContext):
            logger.info(f"执行前: {ctx.action_name}")

        async def after_action(self, ctx: PluginContext):
            logger.info(f"执行后: {ctx.action_name}")
    ```
    """

    def __init__(self, session, page, browser, config: Optional[Dict[str, Any]] = None):
        """
        初始化插件

        Args:
            session: 浏览器会话对象
            page: 当前页面对象
            browser: 浏览器对象
            config: 用户配置
        """
        self.session = session
        self.page = page
        self.browser = browser
        self.config = config or {}
        self.logger: Optional[loguru.Logger] = None
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    @abstractmethod
    def get_metadata(self) -> PluginMetadata:
        """返回插件元数据"""
        ...

    async def on_init(self):
        """插件初始化时调用"""
        pass

    async def on_destroy(self):
        """插件销毁时调用"""
        pass

    async def before_action(self, ctx: PluginContext):
        """操作执行前钩子"""
        pass

    async def after_action(self, ctx: PluginContext):
        """操作执行后钩子"""
        pass

    async def on_error(self, ctx: PluginContext):
        """操作出错时钩子"""
        pass

    async def on_timeout(self, ctx: PluginContext):
        """操作超时时钩子"""
        pass

    async def on_success(self, ctx: PluginContext):
        """操作成功时钩子"""
        pass


class PluginRegistry:
    """
    插件注册表

    管理所有用户自定义插件的注册和调用
    """

    def __init__(self):
        self._plugins: Dict[str, Type[BaseCustomPlugin]] = {}
        self._instances: Dict[str, BaseCustomPlugin] = {}
        self._plugin_configs: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        plugin_class: Type[BaseCustomPlugin],
        config: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        注册插件类

        Args:
            plugin_class: 插件类
            config: 默认配置

        Returns:
            插件ID
        """
        # 创建临时实例获取元数据
        temp_instance = plugin_class(session=None, page=None, browser=None)
        metadata = temp_instance.get_metadata()

        if metadata.id in self._plugins:
            raise ValueError(f"插件 ID {metadata.id} 已存在")

        self._plugins[metadata.id] = plugin_class
        self._plugin_configs[metadata.id] = config or {}

        return metadata.id

    def unregister(self, plugin_id: str):
        """注销插件"""
        if plugin_id in self._instances:
            asyncio.create_task(self._instances[plugin_id].on_destroy())
            del self._instances[plugin_id]
        if plugin_id in self._plugins:
            del self._plugins[plugin_id]
        if plugin_id in self._plugin_configs:
            del self._plugin_configs[plugin_id]

    def create_instance(
        self,
        plugin_id: str,
        session,
        page,
        browser,
        custom_config: Optional[Dict[str, Any]] = None
    ) -> BaseCustomPlugin:
        """
        创建插件实例

        Args:
            plugin_id: 插件ID
            session: 浏览器会话
            page: 当前页面
            browser: 浏览器对象
            custom_config: 自定义配置

        Returns:
            插件实例
        """
        if plugin_id not in self._plugins:
            raise ValueError(f"插件 {plugin_id} 未注册")

        # 合并配置
        config = {**self._plugin_configs.get(plugin_id, {}), **(custom_config or {})}

        instance = self._plugins[plugin_id](session, page, browser, config)
        self._instances[plugin_id] = instance

        return instance

    def get_instance(self, plugin_id: str) -> Optional[BaseCustomPlugin]:
        """获取已创建的插件实例"""
        return self._instances.get(plugin_id)

    def get_all_metadata(self) -> List[PluginMetadata]:
        """获取所有已注册插件的元数据"""
        result = []
        for plugin_class in self._plugins.values():
            temp = plugin_class(session=None, page=None, browser=None)
            result.append(temp.get_metadata())
        return result

    def get_plugins_by_hook(self, hook_type: PluginHookType) -> List[str]:
        """获取注册了特定钩子的所有插件ID"""
        result = []
        for plugin_id, plugin_class in self._plugins.items():
            temp = plugin_class(session=None, page=None, browser=None)
            metadata = temp.get_metadata()
            if hook_type in metadata.hooks:
                result.append(plugin_id)
        return result


# 全局插件注册表实例
plugin_registry = PluginRegistry()
