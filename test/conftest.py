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
async def shared_browser():
    """会话级别的共享浏览器 - 整个测试会话只启动一次"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
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
