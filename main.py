from contextlib import asynccontextmanager
from fastapi import FastAPI
import fastapi_cdn_host
import uvicorn
import sys
import asyncio
from app.routes import setup_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    if sys.platform.startswith('win'):
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
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Browser Automation API", lifespan=lifespan)
    fastapi_cdn_host.patch_docs(app)

    # 设置路由
    setup_routes(app)

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        'main:app',
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
