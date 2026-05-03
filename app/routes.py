from app.models.exceptions.base_exception import BaseException as CustomBaseException
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


def setup_routes(app: FastAPI):
    """设置应用的所有路由和异常处理器"""
    # 注册路由 - 按层级顺序
    # 1. 配置管理层
    app.include_router(browser.router)  # /api/v1/browser/*

    # 2. 运行时管理层
    app.include_router(browser_control.router)  # /api/v1/browser/session/*

    # 3. 系统管理层
    app.include_router(admin.router)  # /api/{admin_base_path}/*

    # 注册异常处理器
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(CustomBaseException, custom_exception_handler)
    app.add_exception_handler(OperationalError, database_connection_handler)
    app.add_exception_handler(DisconnectionError, database_connection_handler)
    app.add_exception_handler(Exception, global_exception_handler)
