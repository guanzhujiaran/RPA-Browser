"""
交互类 Action - Click, Input, Scroll, Wait, Hover, Press, ElementState, SelectOption
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
    PressParams,
    ElementStateParams,
    SelectOptionParams,
    ClickResult,
    InputResult,
    ScrollResult,
    WaitResult,
    HoverResult,
    GetTextResult,
    GetWindowResult,
    PressResult,
    ElementStateResult,
    SelectOptionResult,
)
import asyncio
import contextlib
import time
from loguru import logger
from playwright.async_api import Locator
from app.services.execution.actions.base import ActionResult, BaseAction, register_action_class
from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.enums import (
    ElementStateEnum,
    ElementStateKindEnum,
    LocatorPickEnum,
    MissingPolicyEnum,
    SelectOptionByEnum,
    WaitMatchEnum,
)
from app.services.execution.locator import (
    apply_pick,
    build_candidates,
    build_locator,
    describe_spec,
    resolve_or_skip,
)
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

        locator_spec = validated_params.locator
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

            if locator_spec is not None:
                target = await resolve_or_skip(
                    self.page,
                    locator_spec,
                    validated_params.fallbacks,
                    timeout_ms=timeout,
                    default_pick=LocatorPickEnum.STRICT,
                    missing=validated_params.missing,
                )
                if target is None:
                    return ActionResult(
                        success=True,
                        data=ClickResult(clicked=False),
                        logs=["元素未命中，按 missing=ignore 跳过点击"],
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id,
                        action_name=self.metadata.name,
                    )
                click_kwargs["timeout"] = timeout
                locator = target.locator
                logger.info(
                    f"[ClickAction] Locator 点击参数: {click_kwargs} "
                    f"命中={target.description}"
                )

                if click_count == 2:
                    dblclick_kwargs = click_kwargs.copy()
                    dblclick_kwargs.pop("click_count", None)
                    await locator.dblclick(**dblclick_kwargs)
                else:
                    await locator.click(**click_kwargs)
            else:
                if position is None:
                    raise ValueError("没有 locator 时必须提供 position")

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

        locator_spec = validated_params.locator
        value = validated_params.value
        force = validated_params.force
        timeout = validated_params.timeout
        # 首尾空白规范化（默认关闭，由动作 JSON 显式开启）：
        # 输入类控件对首尾换行/空格敏感（如评论框以换行开头会被站点判为空内容），
        # 这里只做「去首尾空白」这一确定性变换，不改变文本中间的内容。
        if validated_params.trim:
            value = value.strip()

        try:
            # 构建 fill 参数字典（符合 Playwright API）
            fill_kwargs = {}

            if force:
                fill_kwargs["force"] = force

            if timeout != 30000:
                fill_kwargs["timeout"] = timeout

            if locator_spec is not None:
                target = await resolve_or_skip(
                    self.page,
                    locator_spec,
                    validated_params.fallbacks,
                    timeout_ms=timeout,
                    default_pick=LocatorPickEnum.STRICT,
                    missing=validated_params.missing,
                )
                if target is None:
                    return ActionResult(
                        success=True,
                        data=InputResult(value_length=0),
                        logs=["元素未命中，按 missing=ignore 跳过输入"],
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id,
                        action_name=self.metadata.name,
                    )
                fill_kwargs["timeout"] = timeout
                logger.info(
                    f"[InputAction] fill 参数: {fill_kwargs} 命中={target.description}"
                )
                await target.locator.fill(value, **fill_kwargs)
            else:
                # 没有定位器时，使用 page.keyboard.type 直接输入
                logger.info("[InputAction] 无 locator，使用 page.keyboard.type 输入")
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

        locator_spec = validated_params.locator
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

            if locator_spec is not None:
                # 取 `.first`：`scroll_into_view_if_needed` 是单元素 API，命中多个
                # 元素（列表容器）时会因 Playwright 严格模式直接抛
                # `strict mode violation`，滚动一次都不会发生。滚动语义本就是
                # 「把（第一个）目标滚进视野」，多元素只需处理首个即可。
                target = await resolve_or_skip(
                    self.page,
                    locator_spec,
                    validated_params.fallbacks,
                    timeout_ms=timeout,
                    default_pick=LocatorPickEnum.FIRST,
                )
                if target is None:
                    return ActionResult(
                        success=True,
                        data=ScrollResult(scrolled=False),
                        logs=["元素未命中，跳过滚动到元素"],
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id,
                        action_name=self.metadata.name,
                    )
                logger.info(
                    f"[ScrollAction] scroll_into_view_if_needed 参数: {scroll_kwargs} "
                    f"命中={target.description}"
                )
                scroll_kwargs["timeout"] = timeout
                await target.locator.scroll_into_view_if_needed(**scroll_kwargs)
            else:
                # 没有定位器时，滚动整个页面到顶部
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


#: 轮询等待的间隔（毫秒）：仅用于无法走 `.first` 原生等待的状态组合
_WAIT_POLL_INTERVAL_MS = 100
#: 参数缺省超时（与 BaseActionParams.timeout 默认值一致）：等于它时交给 Playwright 默认超时
_WAIT_DEFAULT_TIMEOUT_MS = 30000
#: 未显式指定 `match` 时的匹配范围：出现类按「任一」，消失类按「全部」
_WAIT_DEFAULT_MATCH: dict[ElementStateEnum, WaitMatchEnum] = {
    ElementStateEnum.VISIBLE: WaitMatchEnum.ANY,
    ElementStateEnum.ATTACHED: WaitMatchEnum.ANY,
    ElementStateEnum.HIDDEN: WaitMatchEnum.ALL,
    ElementStateEnum.DETACHED: WaitMatchEnum.ALL,
}
#: 「元素已消失」类状态：0 个命中元素时即视为满足
_WAIT_ABSENT_STATES = (ElementStateEnum.HIDDEN, ElementStateEnum.DETACHED)


class WaitAction(BaseAction[WaitParams]):
    """等待操作（通用能力，不含任何站点逻辑）

    等待语义 = 「选择器命中的一批元素里，是否已满足 `state`」：

    - `visible` / `attached`（`match` 缺省为 `any`）：**任一**命中元素满足即返回。
      走 `locator.first.wait_for` 的 Playwright 原生等待——`wait_for` 是单元素 API，
      不加 `.first` 时选择器一旦命中多个元素（如一条数据一个自定义元素）就会因
      严格模式抛 `strict mode violation`，等待根本没开始；
    - `hidden` / `detached`（`match` 缺省为 `all`）：**全部**消失才算消失，
      0 个命中元素视为已消失。`locator.first.wait_for(state="hidden")` 只盯第一个元素，
      语义不符，改为轮询逐元素判定；
    - `match` 显式指定时覆盖上述默认（如「任意一个消失即返回」）。
    """

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

        target_desc = (
            describe_spec(validated_params.locator)
            if validated_params.locator is not None
            else ""
        )
        state = validated_params.state
        timeout = validated_params.timeout
        match = validated_params.match or _WAIT_DEFAULT_MATCH[state]
        candidates = build_candidates(
            self.page, validated_params.locator, validated_params.fallbacks
        )

        element_found = True
        try:
            if candidates:
                collections = [collection for _, _, collection in candidates]
                logger.info(
                    f"[WaitAction] wait_for 参数: 定位器=[{target_desc}] "
                    f"候选={len(collections)} state={state.value} "
                    f"match={match.value} timeout={timeout}ms"
                )
                if (
                    len(collections) == 1
                    and match is WaitMatchEnum.ANY
                    and state not in _WAIT_ABSENT_STATES
                ):
                    await collections[0].first.wait_for(
                        timeout=timeout if timeout != _WAIT_DEFAULT_TIMEOUT_MS else None,
                        state=state.value,
                    )
                else:
                    await self._poll_until(collections, state, match, timeout)
            else:
                # 没有定位器时，使用固定等待
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
            logger.warning(f"[WaitAction] 等待超时，未满足条件: {target_desc}")
            # 等待被大量用作「状态探针」（元素出现=已点赞/已关注/未登录…），
            # 超时是正常控制流，因此保持 success=True；但把超时详情写进
            # 过程日志（logs），使操作日志的「执行过程日志」可见，
            # 避免「页面改版 / 定位器失效」与「预期未命中」完全无法区分。
            return ActionResult(
                success=True,
                data=WaitResult(element_found=element_found),
                logs=[
                    f"等待超时: 定位器 [{target_desc}] 在 {timeout}ms 内未满足 "
                    f"state={state.value} / match={match.value}"
                    f"（页面未加载 / 定位器失效 / 元素被遮挡 / 预期未命中）"
                ],
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as e:
            return ActionResult(
                success=True,
                error=f"等待异常: {e}",
                logs=[f"等待异常: 定位器 [{target_desc}] - {e}"],
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

    async def _poll_until(
        self,
        locators: List[Locator],
        state: ElementStateEnum,
        match: WaitMatchEnum,
        timeout_ms: float,
    ) -> None:
        """轮询直到满足 `state`（`match` 决定任一/全部），超时抛 Playwright 超时异常。

        只用于 `.first.wait_for` 语义不符的组合（消失类状态、候选链、禁止「任一满足」
        的情况），因此这里按元素逐个判定，命中数量就是定位器实际匹配到的元素个数。
        `match` 的作用范围是**所有候选**：any=任一候选满足即返回；all=所有候选都满足。
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            if await self._satisfied(locators, state, match is WaitMatchEnum.ALL):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PlaywrightTimeoutError(
                    f"等待超时：{timeout_ms}ms 内未满足 state={state.value} "
                    f"match={match.value}"
                )
            await asyncio.sleep(min(_WAIT_POLL_INTERVAL_MS / 1000, remaining))

    async def _satisfied(
        self, locators: List[Locator], state: ElementStateEnum, require_all: bool
    ) -> bool:
        """当前是否满足状态：候选之间按 require_all 组合，单个候选内按元素组合。

        0 个命中元素时，消失类状态直接判定为满足（元素不存在即「已消失」）。
        """
        results: list[bool] = []
        for locator in locators:
            elements = await locator.all()
            if not elements:
                results.append(state in _WAIT_ABSENT_STATES)
                continue
            element_results = [
                await self._element_satisfied(el, state) for el in elements
            ]
            results.append(
                all(element_results) if require_all else any(element_results)
            )
        return any(results) if not require_all else all(results)

    @staticmethod
    async def _element_satisfied(element: Locator, state: ElementStateEnum) -> bool:
        """单个元素是否处于目标状态（与 Playwright 的判定口径一致）。

        `hidden` 对应「不可见或未附着」，因此用 `is_visible()` 取反即可覆盖
        `display:none` / 零尺寸 / 已卸载等情况。
        """
        if state is ElementStateEnum.VISIBLE:
            return await element.is_visible()
        if state is ElementStateEnum.HIDDEN:
            return not await element.is_visible()
        if state is ElementStateEnum.ATTACHED:
            return await element.count() > 0
        return await element.count() == 0


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

        locator_spec = validated_params.locator
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

            if locator_spec is not None:
                target = await resolve_or_skip(
                    self.page,
                    locator_spec,
                    validated_params.fallbacks,
                    timeout_ms=timeout,
                    default_pick=LocatorPickEnum.STRICT,
                    missing=validated_params.missing,
                )
                if target is None:
                    return ActionResult(
                        success=True,
                        data=HoverResult(hovered=False),
                        logs=["元素未命中，按 missing=ignore 跳过悬停"],
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id,
                        action_name=self.metadata.name,
                    )
                hover_kwargs["timeout"] = timeout
                logger.info(
                    f"[HoverAction] hover 参数: {hover_kwargs} 命中={target.description}"
                )
                await target.locator.hover(**hover_kwargs)
            else:
                if position is None:
                    return ActionResult(
                        success=False,
                        error="没有 locator 时必须提供 position",
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

        locator_spec = validated_params.locator
        target_desc = describe_spec(locator_spec) if locator_spec else "<无>"

        try:
            if locator_spec is None:
                raise ValueError("获取元素文本必须提供 locator")

            items = await self._collect_texts(validated_params)
            text = validated_params.separator.join(items)
            logger.info(
                f"[GetTextAction] 获取元素文本: {target_desc} 共 {len(items)} 条"
            )

            # 通用开关：取不到文本（定位器未命中 / 文本为空）时显式失败，
            # 避免「静默空串」把问题带到下游（如昵称为空导致 LLM 提示词缺上下文）。
            if validated_params.fail_on_empty and not text.strip():
                return ActionResult(
                    success=False,
                    error=f"未获取到文本（定位器未命中或文本为空）: {target_desc}",
                    data=GetTextResult(text=text, items=items),
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                    logs=[f"获取文本为空: {target_desc}（fail_on_empty=true）"],
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

        - item_locator 为空：平铺模式，收集 locator 的所有匹配文本
          （``locator.pick`` 留空按 ``all``；显式写 first/last/nth 时只取一个）；
        - 提供 item_locator：以每个条目为范围取 locator 的第一个文本，
          条目内部命中 exclude_locator 时整条跳过（如置顶标记、UP 主徽章等）。
        """
        assert p.locator is not None
        if p.item_locator is None:
            collection = build_locator(self.page, p.locator)
            picked = apply_pick(collection, p.locator, LocatorPickEnum.ALL)
            return list(await picked.all_text_contents())

        items: List[str] = []
        boxes = build_locator(self.page, p.item_locator)
        total = await boxes.count()
        exclude_text = (p.exclude_text or "").strip()
        for i in range(total):
            box = boxes.nth(i)
            if p.exclude_locator is not None:
                excluded = build_locator(box, p.exclude_locator)
                if await excluded.count() > 0:
                    logger.info(
                        f"[GetTextAction] 条目 {i} 命中排除定位器，跳过: "
                        f"{describe_spec(p.exclude_locator)}"
                    )
                    continue
            if exclude_text and p.exclude_text_locator is not None:
                text_node = build_locator(box, p.exclude_text_locator)
                texts = await text_node.all_text_contents()
                if any(t.strip() == exclude_text for t in texts):
                    logger.info(
                        f"[GetTextAction] 条目 {i} 命中排除文本，跳过: {exclude_text}"
                    )
                    continue
            inner = await self._locator_in_box(box, p)
            if inner is None:
                continue
            texts = await inner.all_text_contents()
            if texts:
                items.append(texts[0])
        logger.info(
            f"[GetTextAction] 条目 {total} 个，排除后取到 {len(items)} 条文本"
        )
        return items

    async def _locator_in_box(
        self, box: Locator, p: GetTextParams
    ) -> Locator | None:
        """在单个条目内部按候选链取第一个有命中的定位器（条目内取首个文本）

        条目内部所有候选都没命中时，退化为「条目元素本身」——常见场景是
        ``item_locator`` 与 ``locator`` 指向同一批节点（条目即文本容器），
        此时「条目内再找条目」必然 0 命中。
        """
        assert p.locator is not None
        candidates = build_candidates(box, p.locator, p.fallbacks)
        for index, spec, collection in candidates:
            if await collection.count() > 0:
                if index > 0:
                    logger.info(
                        f"[GetTextAction] 条目内主定位器未命中，退化到候选 {index}: "
                        f"{describe_spec(spec)}"
                    )
                return collection.first
        logger.info(
            f"[GetTextAction] 条目内未命中定位器 {describe_spec(p.locator)}，"
            "改用条目元素本身的文本"
        )
        return box


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


class PressAction(BaseAction[PressParams]):
    """按键操作（可先聚焦定位器目标，无定位器则发给当前焦点）"""

    action_id: BuiltinActionType = BuiltinActionType.PRESS
    action_type: BuiltinActionType = BuiltinActionType.PRESS
    params: PressParams

    async def _execute(self) -> ActionResult[PressResult]:
        start_time = time.time()

        try:
            _, _, params = self.validate_params_with_model(self.params)
        except ValueError as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        locator_spec = params.locator
        target_desc = describe_spec(locator_spec) if locator_spec else "<焦点>"
        modifiers = [str(m) for m in params.modifiers] if params.modifiers else []
        # Playwright 的 press 没有 modifiers 形参：修饰键是写进 key 里的（"Control+A"）。
        # 这里统一把 modifiers 拼成组合键，有/无定位器两种路径口径一致。
        combo = "+".join([*modifiers, params.key]) if modifiers else params.key

        try:
            if locator_spec is not None:
                # Locator.press 内部会先聚焦该元素再按键，等价于「先 focus 再 press」，
                # 因此 contenteditable / 输入框等需要焦点的场景只需提供定位器即可。
                target = await resolve_or_skip(
                    self.page,
                    locator_spec,
                    params.fallbacks,
                    timeout_ms=params.timeout,
                    default_pick=LocatorPickEnum.STRICT,
                    missing=params.missing,
                )
                if target is None:
                    return ActionResult(
                        success=True,
                        data=PressResult(pressed=False),
                        logs=["元素未命中，按 missing=ignore 跳过按键"],
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id,
                        action_name=self.metadata.name,
                    )
                await target.locator.press(combo, timeout=params.timeout)
            else:
                await self.page.keyboard.press(combo)

            logger.info(
                f"[PressAction] 按键完成: combo={combo} 目标={target_desc} "
                f"modifiers={modifiers}"
            )
            return ActionResult(
                success=True,
                data=PressResult(pressed=True),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as e:
            logger.warning(f"[PressAction] 按键操作执行异常: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class ElementStateAction(BaseAction[ElementStateParams]):
    """读取元素状态（属性 / 数量 / 可见 / 可用 / 输入值），输出固定变量 state_value"""

    action_id: BuiltinActionType = BuiltinActionType.ELEMENT_STATE
    action_type: BuiltinActionType = BuiltinActionType.ELEMENT_STATE
    params: ElementStateParams

    async def _execute(self) -> ActionResult[ElementStateResult]:
        start_time = time.time()

        try:
            _, _, params = self.validate_params_with_model(self.params)
        except ValueError as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        locator_spec = params.locator
        target_desc = describe_spec(locator_spec) if locator_spec else "<无>"
        if locator_spec is None:
            return self._handle_missing(params, start_time)

        candidates = build_candidates(self.page, locator_spec, params.fallbacks)
        collection = candidates[0][2]

        try:
            # timeout 只用于「等元素出现（attached）」，不参与判定元素是否「满足某状态」；
            # 0 表示不等待，直接读当前页面状态（探针模式的常用取值）。
            if params.timeout > 0:
                for _, spec, candidate in candidates:
                    with contextlib.suppress(PlaywrightTimeoutError):
                        # 等不到不算失败：是否允许缺失由 missing 策略决定（见下）
                        await candidate.first.wait_for(
                            state="attached", timeout=params.timeout
                        )
                        collection = candidate
                        break

            # 主定位器 0 命中时按候选链退化（探针动作不做额外等待，直接看当前 DOM）
            if await collection.count() == 0:
                for index, spec, candidate in candidates[1:]:
                    if await candidate.count() > 0:
                        logger.info(
                            f"[ElementStateAction] 主定位器未命中，退化到候选 "
                            f"{index}: {describe_spec(spec)}"
                        )
                        collection = candidate
                        break

            count = await collection.count()
            if count == 0:
                return self._handle_missing(params, start_time)

            # count 统计的是整批命中数，不能落到单元素（collection.first 恒为 1）上读
            if params.kind is ElementStateKindEnum.COUNT:
                value: str | int | bool = count
                value_type = "number"
            else:
                value, value_type = await self._read_value(
                    collection.first, params.kind, params.attribute, params.timeout
                )
            logger.info(
                f"[ElementStateAction] 定位器={target_desc} kind={params.kind.value} "
                f"命中={count} value={value!r}"
            )
            return ActionResult(
                success=True,
                data=ElementStateResult(state_value=value, state_type=value_type),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as e:
            logger.warning(f"[ElementStateAction] 读取元素状态异常: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

    def _handle_missing(
        self, params: ElementStateParams, start_time: float
    ) -> ActionResult[ElementStateResult]:
        """元素不存在时的统一出口：missing=ignore 返回空值成功，missing=fail 直接失败。"""
        description = describe_spec(params.locator) if params.locator else "<无>"
        if params.missing is MissingPolicyEnum.FAIL:
            return ActionResult(
                success=False,
                error=f"元素不存在（missing=fail）: {description}",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        empty_value, value_type = self._empty_value(params.kind)
        logger.info(
            f"[ElementStateAction] 元素不存在，按空值返回: 定位器={description} "
            f"kind={params.kind.value}"
        )
        return ActionResult(
            success=True,
            data=ElementStateResult(state_value=empty_value, state_type=value_type),
            execution_time=time.time() - start_time,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )

    @staticmethod
    def _empty_value(kind: ElementStateKindEnum) -> tuple[str | int | bool, str]:
        """元素缺失时的「空值」及其类型标记（与 kind 的取值类型保持一致）。"""
        if kind is ElementStateKindEnum.COUNT:
            return 0, "number"
        if kind in (ElementStateKindEnum.VISIBLE, ElementStateKindEnum.ENABLED):
            return False, "boolean"
        return "", "string"

    @staticmethod
    async def _read_value(
        element: Locator,
        kind: ElementStateKindEnum,
        attribute: str,
        timeout: float,
    ) -> tuple[str | int | bool, str]:
        """按 kind 读取首个命中元素的状态，返回 (值, 类型标记)。"""
        if kind is ElementStateKindEnum.ATTRIBUTE:
            # get_attribute 找不到属性返回 None，统一成空串，便于当探针判空
            return (await element.get_attribute(attribute)) or "", "string"
        if kind is ElementStateKindEnum.INPUT_VALUE:
            kwargs = {"timeout": timeout} if timeout > 0 else {}
            return await element.input_value(**kwargs), "string"
        if kind is ElementStateKindEnum.VISIBLE:
            return await element.is_visible(), "boolean"
        if kind is ElementStateKindEnum.ENABLED:
            return await element.is_enabled(), "boolean"
        # COUNT 在 _execute 里按整批 locator 统计（见上），不会走到这里
        raise ValueError(f"不支持的单元素读取类型: {kind.value}")


class SelectOptionAction(BaseAction[SelectOptionParams]):
    """原生下拉框选择（value / label / index）"""

    action_id: BuiltinActionType = BuiltinActionType.SELECT_OPTION
    action_type: BuiltinActionType = BuiltinActionType.SELECT_OPTION
    params: SelectOptionParams

    async def _execute(self) -> ActionResult[SelectOptionResult]:
        start_time = time.time()

        try:
            _, _, params = self.validate_params_with_model(self.params)
        except ValueError as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        try:
            select_kwargs = self._build_select_kwargs(params)
        except ValueError as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        try:
            if params.locator is None:
                raise ValueError("下拉选择必须提供 locator")
            target = await resolve_or_skip(
                self.page,
                params.locator,
                params.fallbacks,
                timeout_ms=params.timeout,
                default_pick=LocatorPickEnum.STRICT,
                missing=params.missing,
            )
            if target is None:
                return ActionResult(
                    success=True,
                    data=SelectOptionResult(selected=[], selected_count=0),
                    logs=["元素未命中，按 missing=ignore 跳过下拉选择"],
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )
            selected = await target.locator.select_option(
                **select_kwargs, timeout=params.timeout
            )
            logger.info(
                f"[SelectOptionAction] 定位器={target.description} "
                f"by={params.by.value} selected={selected}"
            )
            return ActionResult(
                success=True,
                data=SelectOptionResult(
                    selected=list(selected), selected_count=len(selected)
                ),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as e:
            logger.warning(f"[SelectOptionAction] 下拉选择执行异常: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

    @staticmethod
    def _build_select_kwargs(params: SelectOptionParams) -> Dict[str, Any]:
        """按 by 组装 Playwright select_option 的关键字参数。

        index 模式下 values 是字符串（变量与 JSON 都只有字符串），这里显式转 int，
        非法值给出可读错误而不是把 Playwright 的底层报错抛给用户。
        """
        if params.by is SelectOptionByEnum.LABEL:
            return {"label": params.values}
        if params.by is SelectOptionByEnum.INDEX:
            try:
                return {"index": [int(str(v).strip()) for v in params.values]}
            except ValueError as exc:
                raise ValueError(
                    f"by=index 时 values 必须是从 0 开始的整数下标，当前为 {params.values}"
                ) from exc
        return {"value": params.values}


# 自注册进内置动作注册表（base 的依赖倒置槽位，供控制流策略层按 action_id 查找）

register_action_class(ClickAction)
register_action_class(InputAction)
register_action_class(ScrollAction)
register_action_class(WaitAction)
register_action_class(HoverAction)
register_action_class(GetTextAction)
register_action_class(GetWindowAction)
register_action_class(PressAction)
register_action_class(ElementStateAction)
register_action_class(SelectOptionAction)
