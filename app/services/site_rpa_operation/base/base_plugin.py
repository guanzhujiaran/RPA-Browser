from abc import ABC
from dataclasses import dataclass
from typing import Optional, Callable
from enum import StrEnum

import loguru
from playwright.async_api import BrowserContext

from app.models.RPA_browser.plugin_model import PluginBaseModel
from app.services.RPA_browser.base.base_engines import BaseUndetectedPlaywright


class PluginMethodType(StrEnum):
    """插件方法类型枚举"""
    BEFORE_EXEC = "before_exec"
    AFTER_EXEC = "after_exec"
    ON_EXEC = "on_exec"
    ON_ERROR = "on_error"
    ON_SUCCESS = "on_success"


class OperationNode:
    """操作节点，表示插件中的一个操作"""

    def __init__(self, operation: Callable, name: str = ""):
        self.operation = operation
        self.name = name
        self.next_operation: ['OperationNode'] | None = None


@dataclass
class BasePlugin(ABC):
    base_playwright_engine: BaseUndetectedPlaywright
    session: BrowserContext
    logger: "loguru.Logger"
    conf: PluginBaseModel | None = None
    # 每个生命周期方法的操作链头节点
    before_exec_chain: OperationNode | None = None
    after_exec_chain: OperationNode | None = None
    on_exec_chain: OperationNode | None = None
    on_error_chain: OperationNode | None = None
    on_success_chain: OperationNode | None = None

    def add_operation(self, method_name: PluginMethodType, operation: Callable, name: str = ""):
        """向指定生命周期方法添加操作"""
        new_node = OperationNode(operation, name)
        chain_head = getattr(self, f"{method_name}_chain")

        if chain_head is None:
            setattr(self, f"{method_name}_chain", new_node)
        else:
            current = chain_head
            while current.next_operation:
                current = current.next_operation
            current.next_operation = new_node

    def set_conf(self, conf: PluginBaseModel):
        self.logger.info(f"[PLUGIN] 插件配置更新为: {self.conf} -> {conf}")
        self.conf = conf

    async def execute_operation_chain(self, chain_head: Optional[OperationNode], *args, **kwargs):
        """执行操作链"""
        current = chain_head
        while current:
            if callable(current.operation):
                await current.operation(*args, **kwargs)
            current = current.next_operation

    async def before_exec(self, *args, **kwargs):
        """执行before_exec操作链"""
        await self.execute_operation_chain(self.before_exec_chain, *args, **kwargs)

    async def after_exec(self, *args, **kwargs):
        """执行after_exec操作链"""
        await self.execute_operation_chain(self.after_exec_chain, *args, **kwargs)

    async def on_exec(self, *args, **kwargs):
        """执行on_exec操作链"""
        await self.execute_operation_chain(self.on_exec_chain, *args, **kwargs)

    async def on_error(self, error: Exception, *args, **kwargs):
        """执行on_error操作链"""
        await self.execute_operation_chain(self.on_error_chain, error=error, *args, **kwargs)

    async def on_success(self, *args, **kwargs):
        """执行on_success操作链"""
        await self.execute_operation_chain(self.on_success_chain, *args, **kwargs)
