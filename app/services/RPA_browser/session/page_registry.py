"""浏览器会话内的页面别名注册表。

设计见 ``docs/多页面并行执行计划书.md``：

- 页面用**别名**标识（``main`` / ``open_page(name=...)`` 自定义），步骤通过
  ``params.page`` 显式指定在哪个页执行，不再依赖隐式的 index / current page；
- 页面数达到 ``browser_max_pages_per_context`` 时**等待空位**，绝不自动关闭
  别人的页面（旧 ``new_page`` 会静默关最旧页，可能正好关掉在跑的页）；
- 别名按运行隔离：``owner_run_id`` 记录归属，运行结束只清理自己打开的页。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注
    from botright.playwright_mock import Page

#: 会话键：(mid, browser_id)
SessionKey = tuple[int, int]

#: 默认页面别名（运行开始时绑定会话当前页，不属于任何运行）
DEFAULT_PAGE_ALIAS = "main"

#: 等待空位的默认上限（秒），可被动作参数覆盖
DEFAULT_ACQUIRE_TIMEOUT = 60.0


class PageRegistryError(RuntimeError):
    """页面别名相关业务错误（页面不存在 / 别名重复 / 等待超时）。"""


@dataclass(slots=True)
class PageEntry:
    """一个已命名的页面。"""

    alias: str
    page: Page
    owner_run_id: str
    opened_at: float = field(default_factory=time.monotonic)


class SessionPages:
    """单个浏览器会话的页面集合。"""

    def __init__(self) -> None:
        self.entries: dict[str, PageEntry] = {}
        self._condition: asyncio.Condition | None = None

    @property
    def condition(self) -> asyncio.Condition:
        """懒创建条件变量（确保在当前事件循环内创建）。"""
        if self._condition is None:
            self._condition = asyncio.Condition()
        return self._condition

    def alive_pages(self) -> list[Page]:
        """仍存活的页面（含未登记的、用户手动打开的页）。"""
        pages: list[Page] = []
        for entry in list(self.entries.values()):
            try:
                if not entry.page.is_closed():
                    pages.append(entry.page)
            except Exception:  # noqa: BLE001 - 页面已销毁
                continue
        return pages


class PageRegistry:
    """按 ``(mid, browser_id)`` 管理页面别名。"""

    def __init__(self) -> None:
        self._sessions: dict[SessionKey, SessionPages] = {}

    # ── 别名绑定 ───────────────────────────────────

    def bind_main(self, key: SessionKey, page: Page) -> None:
        """把会话当前页绑定为默认别名 ``main``（运行开始时调用，不属于运行）。"""
        session = self._sessions.setdefault(key, SessionPages())
        session.entries[DEFAULT_PAGE_ALIAS] = PageEntry(
            alias=DEFAULT_PAGE_ALIAS, page=page, owner_run_id=""
        )

    def get(self, key: SessionKey, alias: str) -> Page | None:
        """按别名取页面；不存在或已关闭返回 None。"""
        session = self._sessions.get(key)
        if session is None:
            return None
        entry = session.entries.get(alias)
        if entry is None:
            return None
        try:
            if entry.page.is_closed():
                session.entries.pop(alias, None)
                return None
        except Exception:  # noqa: BLE001 - 页面已销毁
            session.entries.pop(alias, None)
            return None
        return entry.page

    def list_aliases(self, key: SessionKey, run_id: str | None = None) -> list[str]:
        """列出别名（可按归属运行过滤）。"""
        session = self._sessions.get(key)
        if session is None:
            return []
        return [
            alias
            for alias, entry in session.entries.items()
            if run_id is None or entry.owner_run_id == run_id
        ]

    # ── 打开 / 关闭 ─────────────────────────────────

    async def open_page(
        self,
        key: SessionKey,
        *,
        alias: str,
        opener: Page,
        owner_run_id: str,
        url: str | None = None,
        wait_until: str = "load",
        nav_timeout: float = 30000,
        acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
        capacity: int = 8,
        security_check=None,
    ) -> Page:
        """新开页面并绑定别名；容量满时等待，超时抛 ``PageRegistryError``。

        Args:
            opener: 任意一个已存在的页面（用其 ``context`` 新建页面）。
            capacity: 会话内允许的最大页面数（含未登记的页）。
            security_check: 可选的 URL 安全校验协程，返回对象需有 ``allowed`` / ``reason``。
        """
        session = self._sessions.setdefault(key, SessionPages())
        if alias != DEFAULT_PAGE_ALIAS and alias in session.entries:
            if self.get(key, alias) is not None:
                raise PageRegistryError(f"页面别名已存在: {alias}")

        await self._acquire_slot(
            key,
            session,
            opener,
            capacity=capacity,
            timeout=acquire_timeout,
            alias=alias,
        )

        if security_check is not None and url:
            result = await security_check
            if not getattr(result, "allowed", True):
                raise PageRegistryError(getattr(result, "reason", "URL 安全校验未通过"))

        page = await opener.context.new_page()
        try:
            await page.bring_to_front()
        except Exception as exc:  # noqa: BLE001 - 激活失败不影响使用
            logger.debug(f"[PageRegistry] bring_to_front 失败: {exc}")

        if url:
            await page.goto(url, wait_until=wait_until, timeout=nav_timeout)

        session.entries[alias] = PageEntry(
            alias=alias, page=page, owner_run_id=owner_run_id
        )
        logger.info(
            f"[PageRegistry] 打开页面: alias={alias}, owner={owner_run_id or '-'}, "
            f"purpose={'新页面'} , total={len(session.entries)}"
        )
        return page

    async def close_page(
        self, key: SessionKey, *, alias: str, run_id: str | None = None
    ) -> bool:
        """关闭指定别名的页面并唤醒等待者；``main`` 不允许关闭。"""
        if alias == DEFAULT_PAGE_ALIAS:
            raise PageRegistryError("默认页面 main 不允许关闭")
        session = self._sessions.get(key)
        if session is None:
            return False
        entry = session.entries.get(alias)
        if entry is None:
            return False
        if run_id is not None and entry.owner_run_id not in ("", run_id):
            raise PageRegistryError(
                f"页面 {alias} 属于其它运行（{entry.owner_run_id}），不能关闭"
            )
        session.entries.pop(alias, None)
        try:
            if not entry.page.is_closed():
                await entry.page.close()
        except Exception as exc:  # noqa: BLE001 - 页面可能已被外部关闭
            logger.debug(f"[PageRegistry] 关闭页面失败: alias={alias}, {exc}")
        await self._notify(session)
        logger.info(f"[PageRegistry] 关闭页面: alias={alias}, run={run_id or '-'}")
        return True

    async def close_owned(self, key: SessionKey, run_id: str) -> list[str]:
        """关闭某次运行拥有的全部页面（运行结束清理，保留 ``main``）。"""
        session = self._sessions.get(key)
        if session is None:
            return []
        closed: list[str] = []
        for alias, entry in list(session.entries.items()):
            if alias == DEFAULT_PAGE_ALIAS or entry.owner_run_id != run_id:
                continue
            session.entries.pop(alias, None)
            try:
                if not entry.page.is_closed():
                    await entry.page.close()
                closed.append(alias)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[PageRegistry] 清理页面失败: alias={alias}, {exc}")
        if closed:
            await self._notify(session)
            logger.info(f"[PageRegistry] 运行结束清理页面: run={run_id}, aliases={closed}")
        return closed

    def drop_session(self, key: SessionKey) -> None:
        """会话销毁时丢弃登记（浏览器已关闭，页面不可再操作）。"""
        self._sessions.pop(key, None)

    # ── 引用查询 / 回收 / 建页 ───────────────────────────

    def find_alias(self, key: SessionKey, page: Page) -> str | None:
        """反查某页面被哪个别名占用（用于手动关闭前的工作流占用检查）。"""
        session = self._sessions.get(key)
        if session is None:
            return None
        for alias, entry in session.entries.items():
            if entry.page is page:
                return alias
        return None

    def is_referenced(self, key: SessionKey, page: Page) -> bool:
        """该页面是否被工作流/别名引用（被引用则不允许手动关闭）。"""
        return self.find_alias(key, page) is not None

    def unreferenced_pages(self, key: SessionKey, context: Any) -> list[Page]:
        """context 下**未被引用**的存活页面（手动打开、无别名的页）。"""
        session = self._sessions.get(key)
        referenced = (
            {id(entry.page) for entry in session.entries.values()}
            if session is not None
            else set()
        )
        result: list[Page] = []
        try:
            pages = context.pages
        except Exception:  # noqa: BLE001 - context 已销毁
            return result
        for page in pages:
            try:
                if page.is_closed() or id(page) in referenced:
                    continue
            except Exception:  # noqa: BLE001
                continue
            result.append(page)
        return result

    async def create_page(
        self,
        key: SessionKey,
        *,
        opener: Page,
        owner_run_id: str = "",
        url: str | None = None,
        wait_until: str = "load",
        nav_timeout: float = 30000,
        acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
        capacity: int = 8,
    ) -> Page:
        """新建页面（不绑定别名）：满了先回收未标记页，再等待空位。

        回收顺序刻意只挑「未被工作流引用」的页，绝不关闭别名页；无未标记页可回收时
        才等待空位（见 docs/多页面并行执行计划书.md）。
        """
        session = self._sessions.setdefault(key, SessionPages())
        await self._acquire_slot(
            key,
            session,
            opener,
            capacity=capacity,
            timeout=acquire_timeout,
            alias="<新页面>",
        )

        page = await opener.context.new_page()
        try:
            await page.bring_to_front()
        except Exception as exc:  # noqa: BLE001 - 激活失败不影响使用
            logger.debug(f"[PageRegistry] bring_to_front 失败: {exc}")
        if url:
            await page.goto(url, wait_until=wait_until, timeout=nav_timeout)
        logger.info(
            f"[PageRegistry] 新建页面（未标记）: owner={owner_run_id or '-'}, "
            f"total={self._context_alive_count(opener)}"
        )
        return page

    # ── 内部 ─────────────────────────────────────────

    async def _acquire_slot(
        self,
        key: SessionKey,
        session: "SessionPages",
        opener: Page,
        *,
        capacity: int,
        timeout: float,
        alias: str,
        recycle: bool = True,
    ) -> None:
        """为一次建页获取容量：满了**优先回收未标记页面**，再等待空位。

        回收只挑「未被工作流引用」的页（无别名的页），绝不关闭别名页；
        无可回收页时才阻塞等待（见 docs/多页面并行执行计划书.md）。
        """
        alive = self._context_alive_count(opener)
        if alive >= capacity and recycle:
            recyclable = self.unreferenced_pages(key, opener.context)
            for page in recyclable[: alive - capacity + 1]:
                try:
                    await page.close()
                    logger.info("[PageRegistry] 回收未标记页面以腾出空位")
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[PageRegistry] 回收页面失败: {exc}")
            alive = self._context_alive_count(opener)
            if alive < capacity:
                await self._notify(session)

        if alive >= capacity:
            await self._wait_capacity(
                session, opener, capacity=capacity, timeout=timeout, alias=alias
            )

    async def _wait_capacity(
        self,
        session: SessionPages,
        opener: Page,
        *,
        capacity: int,
        timeout: float,
        alias: str,
    ) -> None:
        """等待会话页面数降到容量以下（满则等待，绝不关别人的页）。"""
        deadline = time.monotonic() + max(0.0, timeout)
        async with session.condition:
            while True:
                alive = self._context_alive_count(opener)
                if alive < capacity:
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PageRegistryError(
                        f"等待空闲页面超时：会话已占用 {alive}/{capacity} 个页面"
                        f"（要打开 {alias}，请先 close_page 释放）"
                    )
                logger.warning(
                    f"[PageRegistry] 页面已满 {alive}/{capacity}，等待空位"
                    f"（待打开 {alias}，剩余 {remaining:.0f}s）"
                )
                try:
                    await asyncio.wait_for(session.condition.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    continue

    @staticmethod
    def _context_alive_count(opener: Page) -> int:
        """统计同一 context 下仍存活的页面数（含未登记的页）。"""
        try:
            pages = opener.context.pages
        except Exception:  # noqa: BLE001 - context 已销毁
            return 0
        return len([p for p in pages if not p.is_closed()])

    @staticmethod
    async def _notify(session: SessionPages) -> None:
        """唤醒所有等待空位的协程。"""
        async with session.condition:
            session.condition.notify_all()


#: 进程内单例
page_registry = PageRegistry()

__all__ = [
    "DEFAULT_ACQUIRE_TIMEOUT",
    "DEFAULT_PAGE_ALIAS",
    "PageEntry",
    "PageRegistry",
    "PageRegistryError",
    "SessionKey",
    "page_registry",
]
