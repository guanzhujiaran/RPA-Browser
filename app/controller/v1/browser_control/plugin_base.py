from fastapi import APIRouter

from app.models.router.all_routes import browser_plugin_control_router
from app.utils.controller.router_path import gen_api_router


def new_router(dependencies=None) -> APIRouter:
    """创建 plugin controller 的路由"""
    return gen_api_router(browser_plugin_control_router, dependencies)
