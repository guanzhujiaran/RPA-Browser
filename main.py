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
from app.utils.logging_setup import setup_logging
from app.utils.virtual_display import ensure_virtual_display, stop_virtual_display
import asyncio
from loguru import logger
from app.services.mq.rpc_client import rpc_client
from app.services.mq.rpc_server import start_rpc_server, stop_rpc_server
from app.services.execution.action_log_consumer import (
    start_log_consumer,
    stop_log_consumer,
)
from app.services.RPA_browser.session.session_status_bus import session_status_bus
from app.services.execution.workflow_progress import workflow_run_progress_bus


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

    # 会话状态 SSE 推送：绑定事件循环，供非循环线程发布状态时安全投递
    session_status_bus.bind_loop(asyncio.get_running_loop())
    # 工作流运行进度 SSE 推送：同上（进度上报可能在执行线程投递）
    workflow_run_progress_bus.bind_loop(asyncio.get_running_loop())

    # 参照 FastapiApp lifespan 模式：先执行 alembic upgrade head，再检查 Schema 一致性
    if settings.alembic_auto_migrate:
        if not await run_alembic_upgrade_head():
            raise RuntimeError(
                "alembic upgrade head 执行失败，请检查数据库连接与迁移脚本"
            )
        if not await check_schemas():
            raise RuntimeError(
                "数据库 Schema 与模型不一致，请先手动执行 alembic upgrade head"
            )

    await init_dependencies()

    # 有头模式需要显示环境：无 DISPLAY 时拉起 Xvfb 虚拟屏幕（画面不真的显示）
    await ensure_virtual_display()

    # 启动后台任务
    await start_background_tasks()

    # 连接 RabbitMQ RPC 客户端（HTTP 请求 Action 通过 RPC 调用 FastapiApp 业务方法）
    await rpc_client.connect()

    # 启动 RPA 资源 RPC 服务端（2.18.0：供 be-message 获取资源详情）
    await start_rpc_server()

    # 断点恢复：把「服务重启导致中断」且开启了 resume_on_restart 的运行从
    # checkpoint 之后重新拉起（至少一次语义，循环内资源由处理记录去重）。
    # 放在 RPC 就绪之后：恢复出来的运行会立刻执行步骤，HTTP 类动作依赖 RPC 链路。
    from app.services.execution.workflow_runner import resume_interrupted_runs

    try:
        resumed = await resume_interrupted_runs()
        if resumed:
            logger.warning(
                f"⚠️ 已从断点恢复 {len(resumed)} 条中断的工作流运行: {resumed[:20]}"
            )
    except Exception as exc:  # noqa: BLE001 - 恢复失败不阻塞服务启动
        logger.exception(f"启动断点恢复失败（不阻塞启动）: {exc}")

    # 启动操作日志 MQ 消费端（可选：仅当 action_log_use_mq 开启时）。
    # 启动失败不阻塞服务：发布端在投递失败时会自动回落同步直写（日志不丢），
    # 队列里堆积的消息等消费端下次启动后再被幂等落库。
    if settings.action_log_use_mq:
        try:
            await start_log_consumer()
        except Exception as exc:  # noqa: BLE001 - 日志消费端不可用不阻塞启动
            logger.warning(f"操作日志 MQ 消费端启动失败（期间日志回落同步直写）: {exc}")

    logger.info("lifespan complete!")
    yield
    await stop_background_tasks()
    await stop_virtual_display()
    if settings.action_log_use_mq:
        await stop_log_consumer()
    await stop_rpc_server()
    await rpc_client.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Browser Automation API", lifespan=lifespan)
    fastapi_cdn_host.patch_docs(app)
    setup_routes(app)

    return app


# 日志必须在应用启动前初始化：收敛 loguru 级别 + 接管标准库日志（第三方 INFO 噪声）
setup_logging()

app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=28000,
        reload=False,
    )
