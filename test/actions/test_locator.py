"""定位器（LocatorSpec / resolve_target）单测（离线，不依赖真实浏览器）

覆盖「定位器取代裸 selector」后的通用机制：

- 定位策略派发（css / text / role / testid / label / placeholder / xpath）；
- 候选优先级链（主定位器 0 命中时按顺序退化）；
- 多命中选择（first / last / nth / strict / all）；
- 作用域（within 父元素、frame iframe）；
- 定位即等待（wait_for 的 state / timeout 切分）；
- 缺失策略（missing=fail / ignore）与旧 ``selector`` 入参的兼容迁移。
"""

from __future__ import annotations

import pytest
from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError

from app.models.execution.action_param_models import (
    ClickParams,
    LocatorSpec,
)
from app.models.execution.enums import (
    LocatorPickEnum,
    LocatorStrategyEnum,
    MissingPolicyEnum,
)
from app.services.execution.locator import (
    LocatorNotFoundError,
    build_locator,
    describe_spec,
    resolve_or_skip,
    resolve_target,
)


class FakeLocator:
    """最小可用的 Playwright Locator 替身（只实现定位器解析用到的方法）"""

    def __init__(self, page: "FakePage", path: str, tag: str | None = None) -> None:
        self.page = page
        self.path = path
        #: 被 first / last / nth 收窄时打上的标记（`resolve_target` 返回的
        #: locator 上能直接看出采用了哪种 pick 策略）
        self.tag = tag

    @property
    def _count(self) -> int:
        return self.page.counts.get(self.path, 0)

    async def count(self) -> int:
        return self._count

    async def wait_for(self, state: str | None = None, timeout: float | None = None):
        self.page.waits.append((self.path, state, timeout))
        if self._count == 0:
            raise PlaywrightTimeoutError(f"Timeout {timeout}ms exceeded: {self.path}")

    def locator(self, css: str) -> "FakeLocator":
        return FakeLocator(self.page, f"{self.path} >> {css}")

    def filter(self, has_text: str | None = None, **_kwargs) -> "FakeLocator":
        self.page.filters.append(has_text)
        return FakeLocator(self.page, f"{self.path} ::has_text={has_text}")

    @property
    def first(self) -> "FakeLocator":
        return FakeLocator(self.page, self.path, tag="first")

    @property
    def last(self) -> "FakeLocator":
        return FakeLocator(self.page, self.path, tag="last")

    def nth(self, index: int) -> "FakeLocator":
        return FakeLocator(self.page, self.path, tag=f"nth:{index}")


class FakeFrame:
    def __init__(self, page: "FakePage", css: str) -> None:
        self.page = page
        self.css = css

    def locator(self, css: str) -> FakeLocator:
        return FakeLocator(self.page, f"frame[{self.css}] >> {css}")


class FakePage:
    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.counts = counts or {}
        self.locators: list[str] = []
        self.waits: list[tuple[str, str | None, float | None]] = []
        self.filters: list[str | None] = []
        self.strategy_calls: list[tuple[str, str, bool]] = []

    def locator(self, css: str) -> FakeLocator:
        self.locators.append(css)
        return FakeLocator(self, css)

    def frame_locator(self, css: str) -> FakeFrame:
        return FakeFrame(self, css)

    def get_by_text(self, text: str, exact: bool = False) -> FakeLocator:
        self.strategy_calls.append(("text", text, exact))
        return FakeLocator(self, f"text={text}")

    def get_by_role(self, role: str, exact: bool = False) -> FakeLocator:
        self.strategy_calls.append(("role", role, exact))
        return FakeLocator(self, f"role={role}")

    def get_by_test_id(self, test_id: str) -> FakeLocator:
        self.strategy_calls.append(("testid", test_id, False))
        return FakeLocator(self, f"testid={test_id}")

    def get_by_label(self, label: str, exact: bool = False) -> FakeLocator:
        self.strategy_calls.append(("label", label, exact))
        return FakeLocator(self, f"label={label}")

    def get_by_placeholder(self, text: str, exact: bool = False) -> FakeLocator:
        self.strategy_calls.append(("placeholder", text, exact))
        return FakeLocator(self, f"placeholder={text}")


# ────────────────────────── 策略派发 ──────────────────────────


@pytest.mark.asyncio
async def test_css_is_default_strategy():
    page = FakePage({".btn": 1})
    target = await resolve_target(page, LocatorSpec(value=".btn"), timeout_ms=1000)

    assert page.locators == [".btn"]
    assert target.count == 1
    assert target.candidate_index == 0


@pytest.mark.asyncio
async def test_text_strategy_uses_get_by_text_with_exact():
    page = FakePage({"text=关注": 1})
    spec = LocatorSpec(strategy=LocatorStrategyEnum.TEXT, value="关注", exact=True)
    await resolve_target(page, spec, timeout_ms=1000)

    assert page.strategy_calls == [("text", "关注", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        (LocatorStrategyEnum.ROLE, ("role", "button", False)),
        (LocatorStrategyEnum.TESTID, ("testid", "submit", False)),
        (LocatorStrategyEnum.LABEL, ("label", "昵称", False)),
        (LocatorStrategyEnum.PLACEHOLDER, ("placeholder", "说点什么", False)),
    ],
)
async def test_other_strategies_dispatch(strategy, expected):
    name, value, _exact = expected
    page = FakePage({f"{name}={value}": 1})
    await resolve_target(page, LocatorSpec(strategy=strategy, value=value), timeout_ms=1000)

    assert page.strategy_calls[0][:2] == (name, value)


@pytest.mark.asyncio
async def test_xpath_strategy_prefixes_engine():
    page = FakePage({"xpath=//button": 1})
    await resolve_target(
        page, LocatorSpec(strategy=LocatorStrategyEnum.XPATH, value="//button"), timeout_ms=1000
    )

    assert page.locators == ["xpath=//button"]


@pytest.mark.asyncio
async def test_has_text_filters_locator():
    page = FakePage({".item ::has_text=置顶": 1})
    await resolve_target(page, LocatorSpec(value=".item", has_text="置顶"), timeout_ms=1000)

    assert page.filters == ["置顶"]


# ────────────────────────── 作用域 ──────────────────────────


@pytest.mark.asyncio
async def test_within_scopes_to_parent():
    page = FakePage({"bili-comments >> #pub button": 1})
    spec = LocatorSpec(value="#pub button", within="bili-comments")
    target = await resolve_target(page, spec, timeout_ms=1000)

    assert page.locators == ["bili-comments"]
    assert target.count == 1


@pytest.mark.asyncio
async def test_frame_enters_iframe_first():
    page = FakePage({"frame[iframe#cap] >> .ok": 1})
    spec = LocatorSpec(value=".ok", frame="iframe#cap")
    await resolve_target(page, spec, timeout_ms=1000)

    assert not page.locators, "进入 iframe 后不应再在主页面上定位"


# ────────────────────────── 候选链 ──────────────────────────


@pytest.mark.asyncio
async def test_fallbacks_degrade_in_order():
    page = FakePage({".missing": 0, ".hit": 2})
    target = await resolve_target(
        page,
        LocatorSpec(value=".missing"),
        [LocatorSpec(value=".hit")],
        timeout_ms=2000,
    )

    assert target.candidate_index == 1
    assert target.count == 2
    assert [w[0] for w in page.waits] == [".missing", ".hit"]


@pytest.mark.asyncio
async def test_all_candidates_missing_raises_with_detail():
    page = FakePage()
    with pytest.raises(LocatorNotFoundError) as exc:
        await resolve_target(
            page,
            LocatorSpec(value=".a"),
            [LocatorSpec(value=".b")],
            timeout_ms=500,
        )

    message = str(exc.value)
    assert "css=.a" in message and "css=.b" in message


@pytest.mark.asyncio
async def test_missing_ignore_returns_none():
    page = FakePage()
    target = await resolve_or_skip(
        page,
        LocatorSpec(value=".none"),
        [],
        timeout_ms=200,
        missing=MissingPolicyEnum.IGNORE,
    )

    assert target is None


# ────────────────────────── 多命中选择 ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pick", "nth", "expected_pick"),
    [
        (LocatorPickEnum.FIRST, None, "first"),
        (LocatorPickEnum.LAST, None, "last"),
        (LocatorPickEnum.NTH, 2, "nth:2"),
    ],
)
async def test_pick_narrows_collection(pick, nth, expected_pick):
    page = FakePage({".item": 3})
    target = await resolve_target(
        page, LocatorSpec(value=".item", pick=pick, nth=nth), timeout_ms=1000
    )

    assert target.locator.tag == expected_pick


@pytest.mark.asyncio
async def test_strict_pick_keeps_collection_untouched():
    page = FakePage({".item": 2})
    target = await resolve_target(page, LocatorSpec(value=".item"), timeout_ms=1000)

    assert target.locator.tag is None, "strict 不收窄，交给 Playwright 判多命中"
    assert target.locator is target.collection


@pytest.mark.asyncio
async def test_default_pick_all_keeps_collection_for_reads():
    page = FakePage({".item": 3})
    target = await resolve_target(
        page,
        LocatorSpec(value=".item"),
        timeout_ms=1000,
        default_pick=LocatorPickEnum.ALL,
    )

    assert target.locator is target.collection, "all 保持整批，供读取类动作收集"
    assert target.count == 3


def test_pick_nth_requires_index():
    with pytest.raises(ValueError):
        LocatorSpec(value=".item", pick=LocatorPickEnum.NTH)


# ────────────────────────── 定位即等待 ──────────────────────────


@pytest.mark.asyncio
async def test_state_and_timeout_are_forwarded():
    page = FakePage({".btn": 1})
    spec = LocatorSpec(value=".btn", timeout_ms=800)
    await resolve_target(page, spec, timeout_ms=5000)

    path, state, timeout = page.waits[0]
    assert path == ".btn"
    assert state == "attached", "默认只需等元素进 DOM，可操作性交给 Playwright"
    assert timeout == 800, "spec.timeout_ms 优先于动作总超时"


@pytest.mark.asyncio
async def test_candidate_timeout_is_shared():
    page = FakePage({".a": 0, ".b": 1})
    await resolve_target(
        page,
        LocatorSpec(value=".a"),
        [LocatorSpec(value=".b")],
        timeout_ms=4000,
    )

    first_wait = page.waits[0][2]
    assert first_wait == pytest.approx(2000, abs=50), "两个候选平分总超时"


# ────────────────────────── 描述与旧入参兼容 ──────────────────────────


def test_describe_spec_includes_scope_and_pick():
    spec = LocatorSpec(
        value=".btn", within=".box", frame="iframe#a", pick=LocatorPickEnum.FIRST
    )
    desc = describe_spec(spec)

    assert "frame=iframe#a" in desc and "within=.box" in desc and "pick=first" in desc


def test_legacy_selector_kwarg_migrates_to_locator():
    params = ClickParams(selector="#go")

    assert params.locator is not None
    assert params.locator.value == "#go"
    assert params.locator.strategy is LocatorStrategyEnum.CSS


def test_legacy_selector_does_not_override_locator():
    params = ClickParams(selector="#old", locator={"value": "#new"})

    assert params.locator is not None
    assert params.locator.value == "#new"


@pytest.mark.asyncio
async def test_build_locator_is_pure():
    page = FakePage({".btn": 1})
    build_locator(page, LocatorSpec(value=".btn"))

    assert page.waits == [], "build_locator 只编译不等待"


def test_blank_locator_value_normalizes_to_none():
    """参数表单预填的空壳定位器（value=""）必须归一成「没有定位器」"""
    params = ClickParams(locator={"value": ""})

    assert params.locator is None


@pytest.mark.asyncio
async def test_blank_locator_value_raises_when_built_directly():
    page = FakePage()
    with pytest.raises(ValueError):
        build_locator(page, LocatorSpec(value=""))
