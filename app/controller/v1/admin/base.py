from fastapi import APIRouter

from app.models.router.all_routes import admin_router
from app.utils.controller.router_path import gen_api_router


def new_admin_router(dependencies=None) -> APIRouter:
    return gen_api_router(admin_router, dependencies)

def new_notify_router(dependencies=None) -> APIRouter:
    return gen_api_router(notify_router, dependencies)
