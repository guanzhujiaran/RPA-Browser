from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
import fastapi_cdn_host
import uvicorn
import sys
from app.routes import setup_routes
from app.setup import start_background_tasks, stop_background_tasks
from app.config import settings
from scripts.initd.main import init_dependencies
from app.utils.alembic_migration import run_alembic_upgrade_head, check_schemas
import asyncio
from loguru import logger
from app.services.mq.rpc_client import rpc_client
from app.services.mq.rpc_server import start_rpc_server, stop_rpc_server


def _setup_windows_event_loop() -> None:
    """Windows 平台事件循环配置"""
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

    # 参照 FastapiApp lifespan 模式：先执行 alembic upgrade head，再检查 Schema 一致性
    if settings.alembic_auto_migrate:
        if not await run_alembic_upgrade_head():
            raise RuntimeError("alembic upgrade head 执行失败，请检查数据库连接与迁移脚本")
        if not await check_schemas():
            raise RuntimeError("数据库 Schema 与模型不一致，请先手动执行 alembic upgrade head")

    await init_dependencies()

    # 启动后台任务
    await start_background_tasks()

    # 连接 RabbitMQ RPC 客户端（HTTP 请求 Action 通过 RPC 调用 FastapiApp 业务方法）
    await rpc_client.connect()

    # 启动 RPA 资源 RPC 服务端（2.18.0：供 be-message 获取资源详情）
    await start_rpc_server()

    logger.info("lifespan complete!")
    yield
    await stop_background_tasks()
    await stop_rpc_server()
    await rpc_client.close()


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
        port=28000,
        reload=False,
    )
