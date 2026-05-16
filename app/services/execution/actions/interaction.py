"""
交互类 Action - Click, Input, Scroll, Wait
"""
import asyncio
import time
from loguru import logger

from app.services.execution.actions.base import BaseAction
from app.models.execution.params import (
    ClickParams,
    InputParams,
    ScrollParams,
    WaitParams,
)
from app.models.core.workflow.models import (
    ActionType,
    ActionMetadata,
    ActionResult,
    ActionContext,
)


class ClickAction(BaseAction):
    """点击操作"""

    params_model = ClickParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="click", name="点击", type=ActionType.CLICK,
            description="点击页面元素",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()
        
        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
        
        selector = validated_params.selector
        button = str(validated_params.button)
        click_count = validated_params.click_count
        delay = validated_params.delay
        force = validated_params.force
        modifiers = validated_params.modifiers
        position = validated_params.position
        timeout = validated_params.timeout
        trial = validated_params.trial

        try:
            # 构建 click 参数字典（符合 Playwright API）
            click_kwargs = {"button": button}
            
            if click_count != 1:
                click_kwargs["click_count"] = click_count
            
            if delay > 0:
                click_kwargs["delay"] = delay
            
            if force:
                click_kwargs["force"] = force
            
            if modifiers:
                # 将 KeyboardModifierEnum 转换为字符串列表
                click_kwargs["modifiers"] = [str(m) for m in modifiers]
            
            if position is not None:
                click_kwargs["position"] = {"x": position.x, "y": position.y}
            
            if timeout != 30000:
                click_kwargs["timeout"] = timeout
            
            if trial:
                click_kwargs["trial"] = trial
            
            if selector:
                locator = ctx.page.locator(selector)
                logger.info(f"[ClickAction] Locator 点击参数: {click_kwargs}")
                
                if click_count == 2:
                    await locator.dblclick(**click_kwargs)
                else:
                    await locator.click(**click_kwargs)
            else:
                if position is None:
                    raise ValueError("没有 selector 时必须提供 position")
                
                # page.click 使用 x, y 而不是 position
                page_click_kwargs = click_kwargs.copy()
                if "position" in page_click_kwargs:
                    pos = page_click_kwargs.pop("position")
                    page_click_kwargs["x"] = pos["x"]
                    page_click_kwargs["y"] = pos["y"]
                
                logger.info(f"[ClickAction] Page 点击参数: {page_click_kwargs}")
                
                if click_count == 2:
                    await ctx.page.dblclick(**page_click_kwargs)
                else:
                    await ctx.page.click(**page_click_kwargs)

            return ActionResult(
                success=True, data={"selector": selector, "button": button},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.error(f"[ClickAction] 点击操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )


class InputAction(BaseAction):
    """输入操作"""

    params_model = InputParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="input", name="输入", type=ActionType.INPUT,
            description="向输入框输入文本",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        selector = validated_params.selector
        value = validated_params.value
        force = validated_params.force
        timeout = validated_params.timeout

        try:
            locator = ctx.page.locator(selector)

            # 构建 fill 参数字典（符合 Playwright API）
            fill_kwargs = {}
            
            if force:
                fill_kwargs["force"] = force
            
            if timeout != 30000:
                fill_kwargs["timeout"] = timeout
            
            logger.info(f"[InputAction] fill 参数: {fill_kwargs}")
            await locator.fill(value, **fill_kwargs)

            return ActionResult(
                success=True, data={"selector": selector, "value_length": len(value)},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.error(f"[InputAction] 输入操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )


class ScrollAction(BaseAction):
    """滚动操作"""

    params_model = ScrollParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="scroll", name="滚动", type=ActionType.SCROLL,
            description="滚动页面或元素",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        selector = validated_params.selector
        timeout = validated_params.timeout

        try:
            # 构建 scroll_into_view_if_needed 参数字典
            scroll_kwargs = {}
            
            if timeout != 30000:
                scroll_kwargs["timeout"] = timeout
            
            if selector:
                locator = ctx.page.locator(selector)
                logger.info(f"[ScrollAction] scroll_into_view_if_needed 参数: {scroll_kwargs}")
                await locator.scroll_into_view_if_needed(**scroll_kwargs)
            else:
                # 没有 selector 时，滚动整个页面到顶部
                await ctx.page.evaluate("window.scrollTo(0, 0)")

            return ActionResult(
                success=True, data={"selector": selector},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.error(f"[ScrollAction] 滚动操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )


class WaitAction(BaseAction):
    """等待操作"""

    params_model = WaitParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="wait", name="等待", type=ActionType.WAIT,
            description="等待指定时间或条件",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        selector = validated_params.selector
        state = str(validated_params.state)
        timeout = validated_params.timeout

        try:
            # 构建 wait_for 参数字典（符合 Playwright API）
            wait_kwargs = {"state": state}
            
            if timeout != 30000:
                wait_kwargs["timeout"] = timeout
            
            if selector:
                locator = ctx.page.locator(selector)
                logger.info(f"[WaitAction] wait_for 参数: {wait_kwargs}")
                await locator.wait_for(**wait_kwargs)
            else:
                # 没有 selector 时，使用固定等待
                await asyncio.sleep(timeout / 1000)

            return ActionResult(
                success=True, data={"selector": selector, "state": state},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.error(f"[WaitAction] 等待操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
