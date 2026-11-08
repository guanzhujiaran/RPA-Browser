"""元素定位器解析器（站点无关的通用定位机制）

职责：把动作 JSON 里的 ``LocatorSpec`` 描述解析成可操作的 Playwright ``Locator``。

设计约束（见项目规范「RPA 基础动作通用性规范」）：

- 这里只实现**机制**：策略派发、作用域（父元素 / iframe）、候选链、多命中选择、
  定位即等待、命中信息回传；不含任何站点 / 域名 / 选择器 / 业务判断；
- 业务差异一律由动作 JSON 表达（选哪个元素、几条候选、命中多了取哪个）。

调用关系：``actions/**`` 里的元素级动作统一走 :func:`resolve_target` /
:func:`build_candidates`，不再各自 ``page.locator(...)``。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger
from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import Locator

from app.models.execution.action_param_models import LocatorSpec
from app.models.execution.enums import (
    ElementStateEnum,
    LocatorPickEnum,
    LocatorStrategyEnum,
    MissingPolicyEnum,
)

#: 单个候选定位器的最短等待窗口：候选链里后面的候选不该被压到「测不出命中」
_MIN_CANDIDATE_TIMEOUT_MS = 500.0


class LocatorNotFoundError(RuntimeError):
    """所有候选定位器都没能命中元素（由动作按 missing 策略决定失败或跳过）"""


@dataclass(frozen=True)
class LocatorTarget:
    """定位结果

    - ``locator``：已按 ``pick`` 收窄后的 locator，可直接用于单元素操作；
    - ``collection``：未收窄的候选集合，用于「整批读取 / 计数」类操作；
    - ``candidate_index`` / ``count`` / ``description``：命中信息，写进动作日志，
      便于区分「选择器失效」和「候选链退化」。
    """

    locator: Locator
    collection: Locator
    candidate_index: int
    count: int
    description: str


def describe_spec(spec: LocatorSpec) -> str:
    """把定位器描述成一行可读文本（日志 / 报错信息用）"""
    parts = [f"{spec.strategy.value}={spec.value}"]
    if spec.has_text:
        parts.append(f"has_text={spec.has_text}")
    if spec.within:
        parts.insert(0, f"within={spec.within}")
    if spec.frame:
        parts.insert(0, f"frame={spec.frame}")
    if spec.pick:
        parts.append(f"pick={spec.pick.value}")
    return " ".join(parts)


def build_locator(root, spec: LocatorSpec) -> Locator:
    """按策略把 ``spec`` 编译成 locator（不等待、不收窄）

    ``root`` 可以是 ``Page``、``FrameLocator`` 或 ``Locator``；作用域顺序为
    frame（进 iframe）→ within（父元素）→ 策略（在作用域内定位）。
    """
    if not spec.value.strip():
        raise ValueError(
            f"定位器取值为空（locator.value）：strategy={spec.strategy.value}；"
            "请填写选择器 / 文本 / role 名等定位取值"
        )

    scope = root
    if spec.frame:
        scope = scope.frame_locator(spec.frame)
    if spec.within:
        scope = scope.locator(spec.within)

    strategy = spec.strategy
    if strategy is LocatorStrategyEnum.TEXT:
        locator = scope.get_by_text(spec.value, exact=spec.exact)
    elif strategy is LocatorStrategyEnum.ROLE:
        locator = scope.get_by_role(spec.value, exact=spec.exact)
    elif strategy is LocatorStrategyEnum.TESTID:
        locator = scope.get_by_test_id(spec.value)
    elif strategy is LocatorStrategyEnum.LABEL:
        locator = scope.get_by_label(spec.value, exact=spec.exact)
    elif strategy is LocatorStrategyEnum.PLACEHOLDER:
        locator = scope.get_by_placeholder(spec.value, exact=spec.exact)
    elif strategy is LocatorStrategyEnum.XPATH:
        locator = scope.locator(f"xpath={spec.value}")
    else:
        locator = scope.locator(spec.value)

    if spec.has_text:
        locator = locator.filter(has_text=spec.has_text)
    return locator


def build_candidates(
    root,
    locator: LocatorSpec | None,
    fallbacks: list[LocatorSpec] | None = None,
) -> list[tuple[int, LocatorSpec, Locator]]:
    """构造候选列表：主定位器在前，fallbacks 按声明顺序在后"""
    specs: list[LocatorSpec] = []
    if locator is not None:
        specs.append(locator)
    specs.extend(fallbacks or [])
    return [(i, spec, build_locator(root, spec)) for i, spec in enumerate(specs)]


def apply_pick(
    collection: Locator, spec: LocatorSpec, default_pick: LocatorPickEnum
) -> Locator:
    """按 pick 策略收窄命中集合"""
    pick = spec.pick or default_pick
    if pick is LocatorPickEnum.FIRST:
        return collection.first
    if pick is LocatorPickEnum.LAST:
        return collection.last
    if pick is LocatorPickEnum.NTH:
        return collection.nth(spec.nth or 0)
    # strict / all：都不收窄。strict 交给 Playwright 在调用动作时判多命中，
    # all 由调用方按整批处理。
    return collection


async def resolve_target(
    root,
    locator: LocatorSpec | None,
    fallbacks: list[LocatorSpec] | None = None,
    *,
    timeout_ms: float,
    default_pick: LocatorPickEnum = LocatorPickEnum.STRICT,
    wait_state: ElementStateEnum | None = None,
) -> LocatorTarget:
    """把定位器（含候选链）解析成可操作目标

    行为：
    1. 按候选顺序逐个尝试，命中（达到 ``wait_state``）即返回，0 命中换下一个；
    2. 每个候选的等待时间按「剩余时间 / 剩余候选数」切分，可用 ``spec.timeout_ms`` 覆盖；
    3. 全部候选超时 → 抛 :class:`LocatorNotFoundError`，错误信息列出试过的候选。

    Args:
        root: Page / FrameLocator / Locator
        locator: 主定位器；为空表示没有定位需求（调用方应先自行处理）
        fallbacks: 候选优先级链
        timeout_ms: 本次定位的总超时（毫秒）
        default_pick: ``spec.pick`` 留空时采用的策略
            （单元素动作传 ``STRICT``，读取类动作传 ``ALL``）
        wait_state: 覆盖 ``spec.state`` 的等待状态（``None`` 用 spec 自己的）
    """
    candidates = build_candidates(root, locator, fallbacks)
    if not candidates:
        raise LocatorNotFoundError("没有提供定位器（locator / fallbacks 均为空）")

    deadline = asyncio.get_running_loop().time() + max(0.0, timeout_ms) / 1000.0
    tried: list[str] = []

    for index, spec, collection in candidates:
        state = wait_state or spec.state
        remaining_ms = (deadline - asyncio.get_running_loop().time()) * 1000.0
        share_ms = max(_MIN_CANDIDATE_TIMEOUT_MS, remaining_ms / (len(candidates) - index))
        wait_ms = spec.timeout_ms if spec.timeout_ms is not None else share_ms
        try:
            await collection.first.wait_for(state=state.value, timeout=wait_ms)
        except PlaywrightTimeoutError:
            tried.append(describe_spec(spec))
            logger.info(
                f"[Locator] 候选 {index} 未命中（{wait_ms:.0f}ms）: {describe_spec(spec)}"
            )
            continue

        count = await collection.count()
        description = describe_spec(spec)
        logger.info(
            f"[Locator] 候选 {index} 命中 {count} 个元素: {description}"
        )
        return LocatorTarget(
            locator=apply_pick(collection, spec, default_pick),
            collection=collection,
            candidate_index=index,
            count=count,
            description=description,
        )

    raise LocatorNotFoundError(
        "定位失败，所有候选都没有命中元素：" + " | ".join(tried or ["<空>"])
    )


async def resolve_or_skip(
    root,
    locator: LocatorSpec | None,
    fallbacks: list[LocatorSpec] | None,
    *,
    timeout_ms: float,
    default_pick: LocatorPickEnum = LocatorPickEnum.STRICT,
    missing: MissingPolicyEnum = MissingPolicyEnum.FAIL,
    wait_state: ElementStateEnum | None = None,
) -> LocatorTarget | None:
    """按 ``missing`` 策略解析定位器：命中返回目标，``missing=ignore`` 时未命中返回 None

    动作只需要区分「拿到元素」与「元素不存在且允许跳过」两种情况，
    避免每个动作各写一遍 try/except + 策略判断。
    """
    try:
        return await resolve_target(
            root,
            locator,
            fallbacks,
            timeout_ms=timeout_ms,
            default_pick=default_pick,
            wait_state=wait_state,
        )
    except LocatorNotFoundError:
        if missing is MissingPolicyEnum.IGNORE:
            logger.info("[Locator] 元素不存在，按 missing=ignore 跳过本次操作")
            return None
        raise


async def locator_count(root, locator: LocatorSpec | None, fallbacks: list[LocatorSpec] | None = None) -> int:
    """统计命中数量（不做等待，供探针类动作使用）"""
    candidates = build_candidates(root, locator, fallbacks)
    if not candidates:
        return 0
    total = 0
    for _, _, collection in candidates:
        total = await collection.count()
        if total > 0:
            return total
    return total
