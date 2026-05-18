from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import fastapi_cdn_host
import uvicorn
import sys
import logging
from app.routes import setup_routes
from app.setup import start_background_tasks, stop_background_tasks
from app.config import settings
from app.models.consts.enums import ConfigRunningModeEnum
from scripts.initd.main import init_dependencies
from app.utils.alembic_migration import run_alembic_migrations
import asyncio

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Windows 平台事件循环配置
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

    # 执行 Alembic 数据库迁移
    if settings.alembic_auto_migrate:
        logger.info("🔄 开始数据库迁移检查...")
        try:
            run_alembic_migrations(
                upgrade_to=settings.alembic_upgrade_target,
                auto_upgrade=True,
                auto_generate=True,  # 自动检测并生成迁移
            )
            logger.info("✅ 数据库迁移检查完成")
        except Exception as e:
            logger.error(f"❌ 数据库迁移失败: {e}")
            logger.error("   应用将继续启动，但可能存在数据不一致风险")
            logger.error("   建议：手动执行 './scripts/alembic_manage.sh upgrade' 进行迁移")
    else:
        logger.info("ℹ️  跳过自动数据库迁移（alembic_auto_migrate=False）")

    await init_dependencies()

    # 启动后台任务
    await start_background_tasks()

    try:
        yield
    finally:
        # 停止后台任务
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
