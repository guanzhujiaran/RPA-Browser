from fastapi import APIRouter

from app.models.router.all_routes import video_stream_router
from app.utils.controller.router_path import gen_api_router


def new_router(dependencies=None) -> APIRouter:
    """创建 video stream controller 的路由"""
    return gen_api_router(video_stream_router, dependencies)
