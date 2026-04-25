from fastapi import APIRouter

from app.models.router.all_routes import system_router
from app.utils.controller.router_path import gen_api_router


def new_system_router(dependencies=None) -> APIRouter:
    """创建系统管理路由"""
    return gen_api_router(system_router, dependencies)
