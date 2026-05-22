from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
import fastapi_cdn_host
import uvicorn
import sys
from app.routes import setup_routes
from app.setup import start_background_tasks, stop_background_tasks
from app.config import settings
from scripts.initd.main import init_dependencies
from app.utils.alembic_migration import run_alembic_migrations, run_alembic_migrations_async
import asyncio
from loguru import logger

@logger.catch
async def init_alembic_migration() -> None:
    """
    初始化 Alembic 数据库迁移（从 lifespan 中提取的单独函数）
    
    处理自动迁移检查，支持在已运行的事件循环中异步执行。
    """
    if settings.alembic_auto_migrate:
        logger.info("🔄 开始数据库迁移检查...")
        await run_alembic_migrations_async(
            upgrade_to=settings.alembic_upgrade_target,
            auto_upgrade=True,
            auto_generate=True,  # 自动检测并生成迁移
        )
        logger.info("✅ 数据库迁移检查完成")
    else:
        logger.info("ℹ️  跳过自动数据库迁移（alembic_auto_migrate=False）")


def _setup_windows_event_loop() -> None:
    """
    Windows 平台事件循环配置（从 lifespan 中提取的单独函数）
    """
    if sys.platform.startswith("win"):
        try:
            policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
            if policy is not None:
                asyncio.set_event_loop_policy(policy())
            else:
                try:
                    loop = asyncio.get_event_loop()
                    if not isinstance(loop, asyncio.SelectorEventLoop):
                        asyncio.set_event_loop(asyncio.SelectorEventLoop())
                except Exception:
                    asyncio.set_event_loop(asyncio.SelectorEventLoop())
        except Exception:
            with suppress(Exception):
                asyncio.set_event_loop(asyncio.SelectorEventLoop())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Windows 平台事件循环配置
    _setup_windows_event_loop()

    # 执行 Alembic 数据库迁移（使用提取的异步函数）
    await init_alembic_migration()

    await init_dependencies()

    # 启动后台任务
    await start_background_tasks()
    logger.info("lifespan complete!")
    yield
    await stop_background_tasks()


def create_app() -> FastAPI:
    app = FastAPI(title="Browser Automation API", lifespan=lifespan)
    fastapi_cdn_host.patch_docs(app)
    setup_routes(app)

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
