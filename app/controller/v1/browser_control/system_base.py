from fastapi import APIRouter

from app.models.router.all_routes import system_management_router
from app.utils.controller.router_path import gen_api_router


def new_router(dependencies=None) -> APIRouter:
    """创建 system controller 的路由"""
    return gen_api_router(system_management_router, dependencies)
