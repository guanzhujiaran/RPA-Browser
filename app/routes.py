from app.models.exceptions.base_exception import BaseException as CustomBaseException
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import OperationalError, DisconnectionError

from app.controller.v1.browser import browser_router
from app.controller.v1.browser_control import (
    session_controller,
    browser_operation_controller,
    plugin_controller,
    video_stream_controller,
    system_controller,
    security_controller,
)
from app.controller.v1.browser import notify_router
from app.controller.v1.admin import admin_router, permission_router
from app.exceptions.handlers import (
    http_exception_handler,
    validation_exception_handler,
    custom_exception_handler,
    global_exception_handler,
    database_connection_handler,
)


def setup_routes(app: FastAPI):
    """设置应用的所有路由和异常处理器"""
    # 注册路由
    app.include_router(browser_router.router)
    app.include_router(notify_router.router)
    app.include_router(session_controller.router)
    app.include_router(browser_operation_controller.router)
    app.include_router(plugin_controller.router)
    app.include_router(video_stream_controller.router)
    app.include_router(system_controller.router)
    app.include_router(security_controller.router)
    app.include_router(admin_router.router)
    app.include_router(permission_router.router)

    # 注册异常处理器
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(CustomBaseException, custom_exception_handler)
    app.add_exception_handler(OperationalError, database_connection_handler)
    app.add_exception_handler(DisconnectionError, database_connection_handler)
    app.add_exception_handler(Exception, global_exception_handler)
