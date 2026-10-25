"""页面别名注册表单测（FakePage，不依赖真实浏览器）。"""

from __future__ import annotations

import asyncio

import pytest

from app.services.RPA_browser.session.page_registry import (
    DEFAULT_PAGE_ALIAS,
    PageRegistry,
    PageRegistryError,
)

KEY = (100, 200)


class FakePage:
    """最小 Page 替身：只实现注册表用到的接口。"""

    def __init__(self, context: "FakeContext") -> None:
        self.context = context
        self.closed = False
        self.url = "about:blank"

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    async def bring_to_front(self) -> None:
        return None

    async def goto(self, url: str, **_kwargs) -> None:
        self.url = url


class FakeContext:
    def __init__(self) -> None:
        self._pages: list[FakePage] = []

    @property
    def pages(self) -> list[FakePage]:
        """真实浏览器里关闭的页会立刻从 context.pages 消失。"""
        return [p for p in self._pages if not p.closed]

    async def new_page(self) -> FakePage:
        page = FakePage(self)
        self._pages.append(page)
        return page


@pytest.fixture
def registry() -> PageRegistry:
    return PageRegistry()


@pytest.fixture
def opener() -> FakePage:
    context = FakeContext()
    page = FakePage(context)
    context._pages.append(page)  # noqa: SLF001 - 测试直接填充底层列表
    return page


async def test_open_and_get_alias(registry: PageRegistry, opener: FakePage) -> None:
    """按别名打开后可取回同一页面对象。"""
    page = await registry.open_page(
        KEY,
        alias="job1",
        opener=opener,
        owner_run_id="run-1",
        url="https://example.com",
        capacity=5,
    )

    assert registry.get(KEY, "job1") is page
    assert page.url == "https://example.com"
    assert registry.list_aliases(KEY, "run-1") == ["job1"]


async def test_duplicate_alias_rejected(registry: PageRegistry, opener: FakePage) -> None:
    """同一会话内别名不可重复。"""
    await registry.open_page(
        KEY, alias="job1", opener=opener, owner_run_id="run-1", capacity=5
    )

    with pytest.raises(PageRegistryError):
        await registry.open_page(
            KEY, alias="job1", opener=opener, owner_run_id="run-2", capacity=5
        )


async def test_capacity_waits_instead_of_closing(
    registry: PageRegistry, opener: FakePage
) -> None:
    """页面满且无可回收页时等待；close_page 释放后立即放行。"""
    context = opener.context
    registry.bind_main(KEY, opener)  # main 被引用，不可回收
    assert len(context.pages) == 1  # opener 占 1 个位置

    await registry.open_page(
        KEY, alias="a", opener=opener, owner_run_id="run-1", capacity=2
    )
    assert len(context.pages) == 2  # 已满

    waiter = asyncio.create_task(
        registry.open_page(
            KEY,
            alias="b",
            opener=opener,
            owner_run_id="run-1",
            capacity=2,
            acquire_timeout=10,
        )
    )
    await asyncio.sleep(0.1)
    assert not waiter.done()
    assert len(context.pages) == 2  # 关键：没有被偷偷关闭任何页面

    await registry.close_page(KEY, alias="a")
    await asyncio.wait_for(waiter, timeout=2)
    assert registry.get(KEY, "b") is not None


async def test_acquire_timeout_reports_error(
    registry: PageRegistry, opener: FakePage
) -> None:
    """等待空位超时返回可读错误（不关页）。"""
    registry.bind_main(KEY, opener)  # main 被引用，不可回收
    # opener 占 1 个位置，capacity=2 时再开一个即满
    await registry.open_page(
        KEY, alias="a", opener=opener, owner_run_id="run-1", capacity=2
    )

    with pytest.raises(PageRegistryError) as exc:
        await registry.open_page(
            KEY,
            alias="b",
            opener=opener,
            owner_run_id="run-1",
            capacity=2,
            acquire_timeout=0.05,
        )
    assert "等待空闲页面超时" in str(exc.value)


async def test_close_owned_only_closes_own_pages(
    registry: PageRegistry, opener: FakePage
) -> None:
    """运行结束只清理自己打开的页，main 与其它运行不受影响。"""
    registry.bind_main(KEY, opener)
    await registry.open_page(
        KEY, alias="mine", opener=opener, owner_run_id="run-1", capacity=5
    )
    await registry.open_page(
        KEY, alias="theirs", opener=opener, owner_run_id="run-2", capacity=5
    )

    closed = await registry.close_owned(KEY, "run-1")

    assert closed == ["mine"]
    assert registry.get(KEY, "mine") is None
    assert registry.get(KEY, "theirs") is not None
    assert registry.get(KEY, DEFAULT_PAGE_ALIAS) is opener


async def test_main_cannot_be_closed(registry: PageRegistry, opener: FakePage) -> None:
    """默认页面 main 不允许关闭。"""
    registry.bind_main(KEY, opener)

    with pytest.raises(PageRegistryError):
        await registry.close_page(KEY, alias=DEFAULT_PAGE_ALIAS)


async def test_foreign_page_cannot_be_closed(
    registry: PageRegistry, opener: FakePage
) -> None:
    """不能关闭属于其它运行的页面。"""
    await registry.open_page(
        KEY, alias="theirs", opener=opener, owner_run_id="run-2", capacity=5
    )

    with pytest.raises(PageRegistryError):
        await registry.close_page(KEY, alias="theirs", run_id="run-1")


async def test_full_prefers_recycling_unreferenced_pages(
    registry: PageRegistry, opener: FakePage
) -> None:
    """满了优先回收未标记页，绝不关闭工作流引用的别名页。"""
    registry.bind_main(KEY, opener)  # main 被引用
    await registry.open_page(
        KEY, alias="a", opener=opener, owner_run_id="run-1", capacity=3
    )
    manual = await registry.create_page(KEY, opener=opener, capacity=3)
    assert len(opener.context.pages) == 3  # main + a + manual，已满

    # 再开别名页：应回收 manual（未标记），而不是等待或关闭 a
    await registry.open_page(
        KEY,
        alias="b",
        opener=opener,
        owner_run_id="run-1",
        capacity=3,
        acquire_timeout=1,
    )

    assert manual.is_closed() is True
    assert registry.get(KEY, "a") is not None
    assert registry.get(KEY, "b") is not None
    assert len(opener.context.pages) == 3


async def test_manual_close_of_referenced_page_is_detected(
    registry: PageRegistry, opener: FakePage
) -> None:
    """手动关闭前可按页面对象反查到别名（工作流占用检查）。"""
    registry.bind_main(KEY, opener)
    page = await registry.open_page(
        KEY, alias="job", opener=opener, owner_run_id="run-1", capacity=5
    )

    assert registry.find_alias(KEY, page) == "job"
    assert registry.is_referenced(KEY, page) is True
    # 未被登记的页反查不到
    assert registry.find_alias(KEY, opener) == "main"
