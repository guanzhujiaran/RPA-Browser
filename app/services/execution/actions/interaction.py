"""
交互类 Action - Click, Input, Scroll, Wait, Hover
"""
from typing import Dict, Any, List

from app.models.execution.action_params import (
    WaitParams, HoverParams, ClickParams, InputParams, ScrollParams, GetTextParams,
    ClickResult, InputResult, ScrollResult, WaitResult, HoverResult, GetTextResult,
)
import asyncio
import time
from loguru import logger
from app.services.execution.actions.base import BaseAction, ActionResult
from app.models.database.workflow.models import BuiltinActionType


class ClickAction(BaseAction[ClickParams]):
    """点击操作"""

    action_id: BuiltinActionType = BuiltinActionType.CLICK
    action_type: BuiltinActionType = BuiltinActionType.CLICK
    params: ClickParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: ClickParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[ClickResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid or not validated_params:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        selector = validated_params.selector
        button = validated_params.button
        click_count = validated_params.click_count
        delay = validated_params.delay
        force = validated_params.force
        modifiers = validated_params.modifiers
        position = validated_params.position
        timeout = validated_params.timeout
        trial = validated_params.trial

        try:
            # 构建 click 参数字典（符合 Playwright API）
            click_kwargs:dict[str,Any] = {"button": button}

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
                logger.info(
                    f"[ClickAction] page.mouse.click 到 ({position.x}, {position.y})")

                if click_count == 2:
                    await self.page.mouse.click(position.x, position.y, click_count=2)
                else:
                    await self.page.mouse.click(position.x, position.y)

            return ActionResult(
                success=True, data=ClickResult(clicked=True),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[ClickAction] 点击操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )


class InputAction(BaseAction[InputParams]):
    """输入操作"""

    action_id: BuiltinActionType = BuiltinActionType.INPUT
    action_type: BuiltinActionType = BuiltinActionType.INPUT
    params: InputParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: InputParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[InputResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
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
                logger.info(
                    f"[InputAction] 无 selector，使用 page.keyboard.type 输入")
                await self.page.keyboard.type(value)

            return ActionResult(
                success=True, data=InputResult(value_length=len(value)),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[InputAction] 输入操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )


class ScrollAction(BaseAction[ScrollParams]):
    """滚动操作"""

    action_id: BuiltinActionType = BuiltinActionType.SCROLL
    action_type: BuiltinActionType = BuiltinActionType.SCROLL
    params: ScrollParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: ScrollParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[ScrollResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid or not validated_params:
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
                logger.info(
                    f"[ScrollAction] scroll_into_view_if_needed 参数: {scroll_kwargs}")
                await locator.scroll_into_view_if_needed(**scroll_kwargs)
            else:
                # 没有 selector 时，滚动整个页面到顶部
                await self.page.evaluate("window.scrollTo(0, 0)")

            return ActionResult(
                success=True, data=ScrollResult(scrolled=True),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[ScrollAction] 滚动操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )


class WaitAction(BaseAction[WaitParams]):
    """等待操作"""

    action_id: BuiltinActionType = BuiltinActionType.WAIT
    action_type: BuiltinActionType = BuiltinActionType.WAIT
    params: WaitParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: WaitParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[WaitResult]:
        """
        等待操作从不异常，因为只是等待而已
        """
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid or not validated_params:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        selector = validated_params.selector
        state = validated_params.state
        timeout = validated_params.timeout

        element_found = True
        try:
            if selector:
                locator = self.page.locator(selector)
                logger.info(f"[WaitAction] wait_for 参数: {validated_params}")
                await locator.wait_for(
                    timeout=timeout if timeout != 30000 else None,
                    state=state.value,
                )
            else:
                # 没有 selector 时，使用固定等待
                await asyncio.sleep(timeout / 1000)

            return ActionResult(
                success=True, data=WaitResult(element_found=element_found),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except TimeoutError:
            element_found = False
            logger.warning(f"[WaitAction] 等待超时，未找到元素: {selector}")
            return ActionResult(
                success=True, data=WaitResult(element_found=element_found),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
        except Exception as e:
            return ActionResult(
                success=True, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )


class HoverAction(BaseAction[HoverParams]):
    """悬停操作"""

    action_id: BuiltinActionType = BuiltinActionType.HOVER
    action_type: BuiltinActionType = BuiltinActionType.HOVER
    params: HoverParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: HoverParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[HoverResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
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
                logger.info(
                    f"[HoverAction] page.mouse.move 到 ({position.x}, {position.y})")
                await self.page.mouse.move(position.x, position.y)

            return ActionResult(
                success=True, data=HoverResult(hovered=True),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[HoverAction] 悬停操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )


class GetTextAction(BaseAction[GetTextParams]):
    """获取元素文本"""

    action_id: BuiltinActionType = BuiltinActionType.GET_TEXT
    action_type: BuiltinActionType = BuiltinActionType.GET_TEXT
    params: GetTextParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: GetTextParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[GetTextResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid or not validated_params:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        selector = validated_params.selector
        timeout = validated_params.timeout

        try:
            if not selector:
                raise ValueError("获取元素文本必须提供 selector")

            locator = self.page.locator(selector)
            logger.info(f"[GetTextAction] 获取元素文本: {selector}")

            # 优先使用 visible text, 否则用 text_content
            text = await locator.inner_text(timeout=timeout if timeout != 30000 else None)

            return ActionResult(
                success=True, data=GetTextResult(text=text),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[GetTextAction] 获取文本异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
