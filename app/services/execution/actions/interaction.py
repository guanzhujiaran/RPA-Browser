"""
交互类 Action - Click, Input, Scroll, Wait
"""
import asyncio
import time
from loguru import logger

from app.services.execution.actions.base import BaseAction, ActionResult
from app.models.execution.params import (
    ClickParams,
    InputParams,
    ScrollParams,
    WaitParams,
    HoverParams,
)
from app.models.database.workflow.models import (
    ActionMetadata,
)
from app.models.database.workflow.models import BuiltinActionType


class ClickAction(BaseAction):
    """点击操作"""

    action_id: BuiltinActionType = BuiltinActionType.CLICK

    async def execute(self) -> ActionResult:
        start_time = time.time()
        
        valid, error_msg, validated_params = self.validate_params_with_model(self.params)
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
                locator = self.page.locator(selector)
                logger.info(f"[ClickAction] Locator 点击参数: {click_kwargs}")
                
                if click_count == 2:
                    dblclick_kwargs = click_kwargs.copy()
                    dblclick_kwargs.pop("click_count", None)
                    await locator.dblclick(**dblclick_kwargs)
                else:
                    await locator.click(**click_kwargs)
            else:
                if position is None:
                    raise ValueError("没有 selector 时必须提供 position")
                
                # 使用 page.mouse.click() 直接点击坐标
                logger.info(f"[ClickAction] page.mouse.click 到 ({position.x}, {position.y})")
                
                if click_count == 2:
                    await self.page.mouse.click(position.x, position.y, click_count=2)
                else:
                    await self.page.mouse.click(position.x, position.y)

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

    action_id: BuiltinActionType = BuiltinActionType.INPUT


    async def execute(self) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(self.params)
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
            # 构建 fill 参数字典（符合 Playwright API）
            fill_kwargs = {}
            
            if force:
                fill_kwargs["force"] = force
            
            if timeout != 30000:
                fill_kwargs["timeout"] = timeout
            
            if selector:
                locator = self.page.locator(selector)
                logger.info(f"[InputAction] fill 参数: {fill_kwargs}")
                await locator.fill(value, **fill_kwargs)
            else:
                # 没有 selector 时，使用 page.keyboard.type 直接输入
                logger.info(f"[InputAction] 无 selector，使用 page.keyboard.type 输入")
                await self.page.keyboard.type(value)

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

    action_id: BuiltinActionType = BuiltinActionType.SCROLL


    async def execute(self) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(self.params)
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
                locator = self.page.locator(selector)
                logger.info(f"[ScrollAction] scroll_into_view_if_needed 参数: {scroll_kwargs}")
                await locator.scroll_into_view_if_needed(**scroll_kwargs)
            else:
                # 没有 selector 时，滚动整个页面到顶部
                await self.page.evaluate("window.scrollTo(0, 0)")

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

    action_id: BuiltinActionType = BuiltinActionType.WAIT

    async def execute(self) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(self.params)
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
                locator = self.page.locator(selector)
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


class HoverAction(BaseAction):
    """悬停操作"""

    action_id: BuiltinActionType = BuiltinActionType.HOVER

    async def execute(self) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(self.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        selector = validated_params.selector
        position = validated_params.position
        modifiers = validated_params.modifiers
        force = validated_params.force
        timeout = validated_params.timeout

        try:
            # 构建 hover 参数字典（符合 Playwright API）
            hover_kwargs = {}
            
            if position is not None:
                hover_kwargs["position"] = {"x": position.x, "y": position.y}
            
            if modifiers:
                hover_kwargs["modifiers"] = [str(m) for m in modifiers]
            
            if force:
                hover_kwargs["force"] = force
            
            if timeout != 30000:
                hover_kwargs["timeout"] = timeout
            
            if selector:
                locator = self.page.locator(selector)
                logger.info(f"[HoverAction] hover 参数: {hover_kwargs}")
                await locator.hover(**hover_kwargs)
            else:
                if position is None:
                    return ActionResult(
                        success=False,
                        error="没有 selector 时必须提供 position",
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id, action_name=self.metadata.name,
                    )
                
                # 使用 page.mouse.move() 直接移动鼠标到坐标
                logger.info(f"[HoverAction] page.mouse.move 到 ({position.x}, {position.y})")
                await self.page.mouse.move(position.x, position.y)

            return ActionResult(
                success=True, data={"selector": selector},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.error(f"[HoverAction] 悬停操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
