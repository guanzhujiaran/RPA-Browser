"""
测试配置文件 - 所有测试共享同一个浏览器和页面
数据库使用 SQLite 本地文件进行测试
"""

import os
import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from sqlmodel import SQLModel

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 强制使用 SQLite 进行测试，避免依赖外部 MySQL
os.environ["MYSQL_BROWSER_INFO_URL"] = "sqlite+aiosqlite:///./test.db"

# 导入内置动作汇总表：触发全部动作模块底部的 register_action_class 自注册。
# composite / loop / if_else 按 action_id 查找子步骤动作类（base.get_action_class），
# 只 import control_flow 而不 import 汇总表时注册表是空的，会报「未找到操作: navigate」。
# 生产侧由 engine 导入汇总表，测试侧统一在这里导入，保持同一前提。
import app.services.execution.actions.all_actions  # noqa: F401,E402


def resolve_chromium_executable() -> str | None:
    """解析**本地已下载**的 playwright chromium 可执行文件路径。

    为什么需要它：playwright 包升级后会按自身版本号查找内核目录
    （如 `chromium-1248`），而本机 `~/.cache/ms-playwright/` 里已下载的可能是
    旧目录（如 `chromium-1243`），不显式指定 `executable_path` 就会全部报
    `BrowserType.launch: Executable doesn't exist`。直接复用本地已有的浏览器，
    不必为了跑测试重新下载一份。

    解析顺序：
    1. 环境变量 `RPA_SELECTOR_CHROMIUM`（显式指定，优先级最高）；
    2. `~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome` 中版本最新的一个
       （headless shell 的目录结构不同，不会被误匹配）。
    """
    custom = os.getenv("RPA_SELECTOR_CHROMIUM")
    if custom and Path(custom).exists():
        return custom
    candidates = sorted(
        Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome")
    )
    return str(candidates[-1]) if candidates else None


@pytest.fixture(scope="session")
def chromium_executable() -> str:
    """本地 chromium 路径（会话级）；找不到时跳过测试并给出安装提示。"""
    executable = resolve_chromium_executable()
    if not executable:
        pytest.skip(
            "未找到本地 chromium：请先执行 `uv run playwright install chromium`，"
            "或用环境变量 RPA_SELECTOR_CHROMIUM 指定 chrome 可执行文件路径"
        )
    return executable


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """创建所有数据库表（会话级别，使用 anyio 运行异步代码）"""
    import anyio
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models.database.workflow.models  # noqa: F401
    import app.models.database.browser.info  # noqa: F401
    import app.models.database.notify.models  # noqa: F401

    async def _setup():
        db_url = os.environ["MYSQL_BROWSER_INFO_URL"]
        if db_url.startswith("sqlite"):
            db_path = db_url.replace("sqlite+aiosqlite:///", "")
            if Path(db_path).exists():
                Path(db_path).unlink()
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        await engine.dispose()

    anyio.run(_setup)
    yield


@pytest.fixture(scope="session")
async def shared_browser(chromium_executable: str):
    """会话级别的共享浏览器 - 整个测试会话只启动一次

    - 显式指定 `executable_path` 复用本地已下载的 chromium（见
      `resolve_chromium_executable`），避免 playwright 版本与内核目录不一致时
      直接起不来；
    - 默认 **headless**：无显示服务器的环境（CI / 容器 / 套壳桌面）里 headed
      启动的 `Page.screenshot` 会报 "Unable to capture screenshot"，
      截图类测试全部挂掉；需要 headed 本地调试时设 `RPA_TEST_HEADED=1`。
    """
    headed = os.getenv("RPA_TEST_HEADED", "0") != "0"
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=chromium_executable,
            headless=not headed,
            slow_mo=0,
            args=["--disable-blink-features=AutomationControlled"],
        )
        yield browser
        await browser.close()


@pytest.fixture(scope="session")
async def browser_context(shared_browser: Browser):
    """会话级别的共享浏览器上下文"""
    context = await shared_browser.new_context(
        viewport={"width": 1280, "height": 720},
    )
    yield context
    await context.close()


@pytest.fixture(scope="session")
async def page(browser_context: BrowserContext) -> Page:
    """会话级别的共享页面 - 所有测试在同一页面上执行"""
    page = await browser_context.new_page()
    yield page
    await page.close()
