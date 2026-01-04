from fastapi import APIRouter

from app.config import settings
from app.models.router.all_routes import RouterInfo


def gen_router_prefix(router_info: RouterInfo) -> str:
    return f"{settings.controller_base_path}{router_info.router_prefix}"


def gen_api_router(router_info: RouterInfo, dependencies=None) -> APIRouter:
    router = APIRouter()
    router.tags = [router_info.version_tag, router_info.router_tag]
    router.prefix = gen_router_prefix(router_info)
    # 设置router描述，用于API文档显示
    if router_info.description:
        router.__doc__ = router_info.description

    if dependencies:
        router.dependencies = dependencies
    return router


__all__ = ["gen_api_router"]
