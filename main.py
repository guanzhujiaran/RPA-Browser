from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import asyncio
import fastapi_cdn_host
import uvicorn
import sys
from app.routes import setup_routes
from app.setup import start_background_tasks, stop_background_tasks
from app.config import settings
from scripts.initd.main import init_dependencies


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
            try:
                asyncio.set_event_loop(asyncio.SelectorEventLoop())
            except Exception:
                pass

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

    # 设置路由（必须在静态文件挂载之前）
    setup_routes(app)

    # 🔧 仅在开发环境挂载静态文件服务（用于 WebRTC 调试工具）
    if settings.environment == "development":
        static_dir = Path(__file__).parent
        if static_dir.exists():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
