"""
用户自定义插件示例

这个文件展示了如何创建和使用用户自定义插件

1. 创建自定义插件
2. 注册插件到注册表
3. 在操作执行中使用插件
"""

import asyncio
from typing import Any, Dict

from app.services.execution import (
    BaseCustomPlugin,
    PluginMetadata,
    PluginHookType,
    PluginContext,
    plugin_registry,
    action_registry,
    ActionMetadata,
    ActionType,
    ActionContext,
    ActionResult,
    BaseAction,
)


# ============ 示例 1: 创建自定义插件 ============

class LoggingPlugin(BaseCustomPlugin):
    """
    日志记录插件

    记录所有操作的执行情况
    """

    def get_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="logging_plugin",
            name="日志记录插件",
            version="1.0.0",
            author="User",
            description="记录所有操作的执行日志",
            hooks=[
                PluginHookType.BEFORE_ACTION,
                PluginHookType.AFTER_ACTION,
                PluginHookType.ON_SUCCESS,
                PluginHookType.ON_ERROR,
            ],
            priority=100,
        )

    async def before_action(self, ctx: PluginContext):
        """操作执行前记录"""
        self.logger.info(f"[LoggingPlugin] 即将执行: {ctx.action_name}")
        self.logger.debug(f"[LoggingPlugin] 参数: {ctx.action_params}")

    async def after_action(self, ctx: PluginContext):
        """操作执行后记录"""
        self.logger.info(f"[LoggingPlugin] 执行完成: {ctx.action_name}, 耗时: {ctx.execution_time:.2f}s")

    async def on_success(self, ctx: PluginContext):
        """操作成功时记录"""
        self.logger.info(f"[LoggingPlugin] ✅ 操作成功: {ctx.action_name}")
        if ctx.result:
            self.logger.debug(f"[LoggingPlugin] 结果: {ctx.result}")

    async def on_error(self, ctx: PluginContext):
        """操作失败时记录"""
        self.logger.error(f"[LoggingPlugin] ❌ 操作失败: {ctx.action_name}, 错误: {ctx.error}")


class RandomWaitPlugin(BaseCustomPlugin):
    """
    随机等待插件

    在操作执行前随机等待一段时间，模拟人类行为
    """

    def get_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="random_wait_plugin",
            name="随机等待插件",
            version="1.0.0",
            author="User",
            description="在操作执行前随机等待，模拟人类行为",
            hooks=[PluginHookType.BEFORE_ACTION],
            priority=50,  # 优先级较高，在其他插件之前执行
            config_schema={
                "min_wait": {"type": "float", "default": 0.5, "min": 0, "max": 10},
                "max_wait": {"type": "float", "default": 3.0, "min": 0, "max": 30},
            }
        )

    async def before_action(self, ctx: PluginContext):
        """随机等待"""
        import random
        min_wait = self.config.get("min_wait", 0.5)
        max_wait = self.config.get("max_wait", 3.0)
        wait_time = random.uniform(min_wait, max_wait)
        self.logger.info(f"[RandomWaitPlugin] 随机等待 {wait_time:.2f}s")
        await asyncio.sleep(wait_time)


class ScreenshotOnErrorPlugin(BaseCustomPlugin):
    """
    错误截图插件

    当操作失败时自动截图
    """

    def get_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="screenshot_on_error_plugin",
            name="错误截图插件",
            version="1.0.0",
            author="User",
            description="操作失败时自动截图保存",
            hooks=[PluginHookType.ON_ERROR],
            priority=200,
        )

    async def on_error(self, ctx: PluginContext):
        """出错时截图"""
        self.logger.warning(f"[ScreenshotOnErrorPlugin] 操作失败，尝试截图...")

        try:
            if self.page:
                import base64
                screenshot_bytes = await self.page.screenshot()
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode()
                ctx.user_data["error_screenshot"] = screenshot_base64
                self.logger.info(f"[ScreenshotOnErrorPlugin] 截图已保存")
        except Exception as e:
            self.logger.error(f"[ScreenshotOnErrorPlugin] 截图失败: {e}")


# ============ 示例 2: 创建自定义操作 ============

class HoverAction(BaseAction):
    """
    悬停操作

    鼠标悬停在指定元素上
    """

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="hover",
            name="悬停",
            type=ActionType.HOVER,
            description="鼠标悬停在指定元素上",
            parameters=[
                {
                    "name": "selector",
                    "type": str,
                    "required": True,
                    "description": "元素选择器"
                },
                {
                    "name": "position",
                    "type": dict,
                    "required": False,
                    "default": None,
                    "description": "悬停位置: {x: 0-1, y: 0-1}"
                },
            ]
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        import time
        start_time = time.time()

        selector = ctx.params.get("selector")
        position = ctx.params.get("position")

        try:
            if position:
                # 使用相对坐标
                viewport = ctx.page.viewport_size or await ctx.page.evaluate(
                    """() => ({width: window.innerWidth, height: window.innerHeight})"""
                )
                abs_x = int(position["x"] * viewport["width"])
                abs_y = int(position["y"] * viewport["height"])
                await ctx.page.hover(x=abs_x, y=abs_y)
            else:
                await ctx.page.locator(selector).hover()

            return ActionResult(
                success=True,
                data={"selector": selector},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


# ============ 示例 3: 注册插件和操作 ============

def register_user_plugins():
    """
    注册用户自定义插件

    建议: 在应用启动时调用此函数
    """
    # 注册插件
    plugin_registry.register(LoggingPlugin, config={"log_level": "INFO"})
    plugin_registry.register(RandomWaitPlugin, config={"min_wait": 1.0, "max_wait": 3.0})
    plugin_registry.register(ScreenshotOnErrorPlugin)

    # 注册自定义操作
    action_registry.register(HoverAction)

    print("[插件系统] 用户自定义插件注册完成")
    print(f"[插件系统] 已注册插件: {[p.id for p in plugin_registry.get_all_metadata()]}")
    print(f"[插件系统] 已注册操作: {[a.id for a in action_registry.get_all_actions()]}")

    return {
        "plugins": [p.id for p in plugin_registry.get_all_metadata()],
        "actions": [a.id for a in action_registry.get_all_actions()],
    }


# ============ 使用示例 ============

async def example_usage():
    """
    使用示例

    展示如何执行操作和工作流
    """
    from app.services.execution import execution_engine, workflow_manager

    # 注册插件和操作
    register_user_plugins()

    # 创建工作流
    workflow = workflow_manager.create_workflow(
        name="测试工作流",
        description="这是一个测试工作流",
        tags=["test", "demo"],
        steps=[
            {
                "action_id": "navigate",
                "params": {"url": "https://example.com"}
            },
            {
                "action_id": "wait",
                "params": {"duration": 1000}
            },
            {
                "action_id": "click",
                "params": {"selector": "#button"}
            },
        ]
    )

    print(f"[示例] 创建工作流: {workflow.metadata.name} (ID: {workflow.workflow.id})")

    # 获取所有可用操作
    print("\n[示例] 所有可用操作:")
    for action in action_registry.get_all_actions():
        print(f"  - {action.id}: {action.name} ({action.type})")

    # 获取所有插件
    print("\n[示例] 所有已注册插件:")
    for plugin in plugin_registry.get_all_metadata():
        print(f"  - {plugin.id}: {plugin.name} (hooks: {plugin.hooks})")

    # 注意: 实际执行需要浏览器上下文，这里仅展示 API 用法
    print("\n[示例] 请在实际的浏览器会话中使用执行引擎")


if __name__ == "__main__":
    asyncio.run(example_usage())
