"""
交互类 Action - Click, Input, Scroll, Wait, Hover
"""

from typing import Dict, Any, List

from app.models.execution.action_params import (
    WaitParams,
    HoverParams,
    ClickParams,
    InputParams,
    ScrollParams,
    GetTextParams,
    GetWindowParams,
    ClickResult,
    InputResult,
    ScrollResult,
    WaitResult,
    HoverResult,
    GetTextResult,
    GetWindowResult,
)
import asyncio
import time
from loguru import logger
from app.services.execution.actions.base import ActionResult, BaseAction, register_action_class
from app.models.database.workflow.models import BuiltinActionType
from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError


class ClickAction(BaseAction[ClickParams]):
    """点击操作"""

    action_id: BuiltinActionType = BuiltinActionType.CLICK
    action_type: BuiltinActionType = BuiltinActionType.CLICK
    params: ClickParams

    async def _execute(self) -> ActionResult[ClickResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or not validated_params:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
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
            click_kwargs: dict[str, Any] = {"button": button}

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
                    f"[ClickAction] page.mouse.click 到 ({position.x}, {position.y})"
                )

                if click_count == 2:
                    await self.page.mouse.click(position.x, position.y, click_count=2)
                else:
                    await self.page.mouse.click(position.x, position.y)

            return ActionResult(
                success=True,
                data=ClickResult(clicked=True),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[ClickAction] 点击操作执行异常: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class InputAction(BaseAction[InputParams]):
    """输入操作"""

    action_id: BuiltinActionType = BuiltinActionType.INPUT
    action_type: BuiltinActionType = BuiltinActionType.INPUT
    params: InputParams

    async def _execute(self) -> ActionResult[InputResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or validated_params is None:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
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
                success=True,
                data=InputResult(value_length=len(value)),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[InputAction] 输入操作执行异常: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class ScrollAction(BaseAction[ScrollParams]):
    """滚动操作"""

    action_id: BuiltinActionType = BuiltinActionType.SCROLL
    action_type: BuiltinActionType = BuiltinActionType.SCROLL
    params: ScrollParams

    async def _execute(self) -> ActionResult[ScrollResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or not validated_params:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        selector = validated_params.selector
        timeout = validated_params.timeout

        try:
            # ── 滚轮模式：x / y 任一提供 → 原生滚轮相对滚动 ──
            # 为什么不用 window.scrollBy：mouse.wheel 是浏览器原生输入事件，
            # 会正常触发 IntersectionObserver / scroll 监听（懒加载、无限滚动），
            # 且不依赖任何站点选择器，属于与业务无关的通用能力。
            if validated_params.x is not None or validated_params.y is not None:
                dx_total = int(validated_params.x or 0)
                dy_total = int(validated_params.y or 0)
                steps = max(1, int(validated_params.steps))
                interval = max(0, int(validated_params.interval_ms)) / 1000.0
                # 除法余数补到最后一步，保证累计位移精确等于 y（整数像素）
                dx_base, dx_rem = divmod(dx_total, steps)
                dy_base, dy_rem = divmod(dy_total, steps)
                for i in range(steps):
                    dx = dx_base + (dx_rem if i == steps - 1 else 0)
                    dy = dy_base + (dy_rem if i == steps - 1 else 0)
                    if dx or dy:
                        await self.page.mouse.wheel(dx, dy)
                    if i < steps - 1 and interval:
                        await asyncio.sleep(interval)
                logger.info(
                    f"[ScrollAction] mouse.wheel 分 {steps} 步滚动: "
                    f"x={dx_total}, y={dy_total}, interval={validated_params.interval_ms}ms"
                )
                return ActionResult(
                    success=True,
                    data=ScrollResult(scrolled=True),
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )

            # 构建 scroll_into_view_if_needed 参数字典
            scroll_kwargs = {}

            if timeout != 30000:
                scroll_kwargs["timeout"] = timeout

            if selector:
                locator = self.page.locator(selector)
                logger.info(
                    f"[ScrollAction] scroll_into_view_if_needed 参数: {scroll_kwargs}"
                )
                await locator.scroll_into_view_if_needed(**scroll_kwargs)
            else:
                # 没有 selector 时，滚动整个页面到顶部
                await self.page.evaluate("window.scrollTo(0, 0)")

            return ActionResult(
                success=True,
                data=ScrollResult(scrolled=True),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[ScrollAction] 滚动操作执行异常: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class WaitAction(BaseAction[WaitParams]):
    """等待操作"""

    action_id: BuiltinActionType = BuiltinActionType.WAIT
    action_type: BuiltinActionType = BuiltinActionType.WAIT
    params: WaitParams

    async def _execute(self) -> ActionResult[WaitResult]:
        """
        等待操作从不异常，因为只是等待而已
        """
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or not validated_params:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
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
                success=True,
                data=WaitResult(element_found=element_found),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except (TimeoutError, PlaywrightTimeoutError):
            element_found = False
            logger.warning(f"[WaitAction] 等待超时，未找到元素: {selector}")
            # 等待被大量用作「状态探针」（元素出现=已点赞/已关注/未登录…），
            # 超时是正常控制流，因此保持 success=True；但把超时详情写进
            # 过程日志（logs），使操作日志的「执行过程日志」可见，
            # 避免「页面改版 / 选择器失效」与「预期未命中」完全无法区分。
            return ActionResult(
                success=True,
                data=WaitResult(element_found=element_found),
                logs=[
                    f"等待超时: 选择器 [{selector}] 在 {timeout}ms 内未满足 "
                    f"state={state.value}（页面未加载 / 选择器失效 / 元素被遮挡 / 预期未命中）"
                ],
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as e:
            return ActionResult(
                success=True,
                error=f"等待异常: {e}",
                logs=[f"等待异常: 选择器 [{selector}] - {e}"],
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class HoverAction(BaseAction[HoverParams]):
    """悬停操作"""

    action_id: BuiltinActionType = BuiltinActionType.HOVER
    action_type: BuiltinActionType = BuiltinActionType.HOVER
    params: HoverParams

    async def _execute(self) -> ActionResult[HoverResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
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
                        action_id=self.metadata.id,
                        action_name=self.metadata.name,
                    )

                # 使用 page.mouse.move() 直接移动鼠标到坐标
                logger.info(
                    f"[HoverAction] page.mouse.move 到 ({position.x}, {position.y})"
                )
                await self.page.mouse.move(position.x, position.y)

            return ActionResult(
                success=True,
                data=HoverResult(hovered=True),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[HoverAction] 悬停操作执行异常: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class GetTextAction(BaseAction[GetTextParams]):
    """获取元素文本"""

    action_id: BuiltinActionType = BuiltinActionType.GET_TEXT
    action_type: BuiltinActionType = BuiltinActionType.GET_TEXT
    params: GetTextParams

    async def _execute(self) -> ActionResult[GetTextResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or not validated_params:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        selector = validated_params.selector

        try:
            if not selector:
                raise ValueError("获取元素文本必须提供 selector")

            items = await self._collect_texts(validated_params)
            text = validated_params.separator.join(items)
            logger.info(
                f"[GetTextAction] 获取元素文本: {selector} 共 {len(items)} 条"
            )

            # 通用开关：取不到文本（选择器未命中 / 文本为空）时显式失败，
            # 避免「静默空串」把问题带到下游（如昵称为空导致 LLM 提示词缺上下文）。
            if validated_params.fail_on_empty and not text.strip():
                return ActionResult(
                    success=False,
                    error=(
                        f"未获取到文本（选择器未命中或文本为空）: {selector}"
                    ),
                    data=GetTextResult(text=text, items=items),
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                    logs=[f"获取文本为空: {selector}（fail_on_empty=true）"],
                )

            return ActionResult(
                success=True,
                data=GetTextResult(text=text, items=items),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[GetTextAction] 获取文本异常: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

    async def _collect_texts(self, p: GetTextParams) -> List[str]:
        """按条目收集文本：一个条目一条，便于直接交给 random_pick 随机取一条

        - item_selector 为空：平铺模式，收集 selector 的所有匹配文本（原有行为）；
        - 提供 item_selector：以每个条目为范围取 selector 的第一个文本，
          条目内部命中 exclude_selector 时整条跳过（如置顶标记、UP 主徽章等）。
        """
        if not p.item_selector:
            return list(await self.page.locator(p.selector).all_text_contents())

        items: List[str] = []
        boxes = self.page.locator(p.item_selector)
        total = await boxes.count()
        exclude_text = (p.exclude_text or "").strip()
        for i in range(total):
            box = boxes.nth(i)
            if p.exclude_selector and await box.locator(p.exclude_selector).count() > 0:
                logger.info(
                    f"[GetTextAction] 条目 {i} 命中排除选择器，跳过: {p.exclude_selector}"
                )
                continue
            if exclude_text and p.exclude_text_selector:
                texts = await box.locator(p.exclude_text_selector).all_text_contents()
                if any(t.strip() == exclude_text for t in texts):
                    logger.info(
                        f"[GetTextAction] 条目 {i} 命中排除文本，跳过: {exclude_text}"
                    )
                    continue
            texts = await box.locator(p.selector).all_text_contents()
            if texts:
                items.append(texts[0])
        logger.info(
            f"[GetTextAction] 条目 {total} 个，排除后取到 {len(items)} 条文本"
        )
        return items


class GetWindowAction(BaseAction[GetWindowParams]):
    """获取 window 对象属性（安全模式，禁止函数调用）"""

    action_id: BuiltinActionType = BuiltinActionType.GET_WINDOW
    action_type: BuiltinActionType = BuiltinActionType.GET_WINDOW
    params: GetWindowParams

    async def _execute(self) -> ActionResult[GetWindowResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or not validated_params:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        try:
            if validated_params.object_name is not None:
                # object_name 模式：获取 window 对象，遍历所有字段，返回非 null 非 undefined 的值
                js_expr = (
                    "(() => {"
                    f"  const obj = window.{validated_params.object_name};"
                    "  if (obj == null) return {};"
                    "  const result = {};"
                    "  for (const key of Object.keys(obj)) {"
                    "    const val = obj[key];"
                    "    if (val !== null && val !== undefined) {"
                    "      result[key] = String(val);"
                    "    }"
                    "  }"
                    "  return result;"
                    "})()"
                )
                result = await self.page.evaluate(js_expr)
                # result 此时是一个 dict[str, str]
                values_dict: dict[str, str] = result if isinstance(result, dict) else {}

                logger.info(
                    f"[GetWindowAction] window.{validated_params.object_name} 遍历字段: "
                    f"{len(values_dict)} 个非空值"
                )

                return ActionResult(
                    success=True,
                    data=GetWindowResult(values=values_dict),
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )

            # property_path 模式：获取单个属性值
            property_path = validated_params.property_path
            js_expr = f"window.{property_path}"
            result = await self.page.evaluate(js_expr)

            # 将结果转为字符串（兼容各种类型）
            if result is None:
                value_str = ""
            elif isinstance(result, str):
                value_str = result
            else:
                value_str = str(result)

            logger.info(f"[GetWindowAction] window.{property_path} = {value_str}")

            return ActionResult(
                success=True,
                data=GetWindowResult(value=value_str),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            logger.warning(f"[GetWindowAction] 获取 window 属性异常: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


# 自注册进内置动作注册表（base 的依赖倒置槽位，供控制流策略层按 action_id 查找）

register_action_class(ClickAction)
register_action_class(InputAction)
register_action_class(ScrollAction)
register_action_class(WaitAction)
register_action_class(HoverAction)
register_action_class(GetTextAction)
register_action_class(GetWindowAction)
