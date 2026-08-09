from app.models.common.exceptions.base_exception import BaseException as CustomBaseException
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import OperationalError, DisconnectionError

# 导入 controller 包（自动收集路由）
from app.controller.v1 import browser, browser_control, admin
from app.exceptions.handlers import (
    http_exception_handler,
    validation_exception_handler,
    custom_exception_handler,
    global_exception_handler,
    database_connection_handler,
)
from app.utils.middlewares.ban_guard import BanGuardMiddleware
from bili_common.exceptions import register_business_exception_handlers


def setup_routes(app: FastAPI):
    """设置应用的所有路由和异常处理器"""
    # 封禁拦截：被封禁用户的请求在进入业务逻辑前直接返回 403
    app.add_middleware(BanGuardMiddleware)

    # 注册路由 - 按层级顺序
    # 1. 配置管理层
    app.include_router(browser.router)

    # # 2. 运行时管理层
    app.include_router(browser_control.router)

    # 3. 系统管理层
    app.include_router(admin.router)

    # 注册异常处理器
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError,
                              validation_exception_handler)
    app.add_exception_handler(CustomBaseException, custom_exception_handler)
    app.add_exception_handler(OperationalError, database_connection_handler)
    app.add_exception_handler(DisconnectionError, database_connection_handler)
    app.add_exception_handler(Exception, global_exception_handler)

    # 接入 bili_common 统一业务异常（如未登录 code=-101，HTTP 200）。
    # 注意：RPA 自行处理 StarletteHTTPException / RequestValidationError，
    # 因此仅注册业务异常处理器，避免覆盖既有 handler。
    register_business_exception_handlers(app)
