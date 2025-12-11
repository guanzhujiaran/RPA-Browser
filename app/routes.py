from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
import os

from app.controller.v1.browser import browser_router
from app.controller.v1.browser import plugin_router
from app.controller.v1.browser import notify_router
from app.controller.v1.browser import auth_router
from app.controller.v1.browser_control import live_controller
from app.exceptions.handlers import http_exception_handler, validation_exception_handler, global_exception_handler


def setup_routes(app: FastAPI):
    """设置应用的所有路由和异常处理器"""
    # 注册静态文件服务
    static_dir = "/home/minato_aqua/bili-fastapi-browser-rpa/static"
    print(f"Looking for static directory at: {static_dir}")
    print(f"Directory exists: {os.path.exists(static_dir)}")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        print("Static files mounted successfully!")
    else:
        print(f"Static directory not found: {static_dir}")
    
    # 注册路由
    app.include_router(browser_router.router)
    app.include_router(plugin_router.router)
    app.include_router(notify_router.router)
    app.include_router(auth_router.router)
    app.include_router(live_controller.router)

    # 注册异常处理器
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)