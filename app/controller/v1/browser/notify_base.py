from fastapi import APIRouter

from app.models.router.all_routes import notify_router
from app.utils.controller.router_path import gen_api_router


def new_router(dependencies=None) -> APIRouter:
    return gen_api_router(notify_router, dependencies)