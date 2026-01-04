from fastapi import APIRouter

from app.models.router.all_routes import security_check_router
from app.utils.controller.router_path import gen_api_router


def new_router(dependencies=None) -> APIRouter:
    """创建 security controller 的路由"""
    return gen_api_router(security_check_router, dependencies)
