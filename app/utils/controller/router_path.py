from fastapi import APIRouter

from app.config import settings
from app.models.router.all_routes import RouterInfo
from app.models.router.router_prefix import RouterPrefix


def gen_router_prefix(router_info: RouterInfo) -> str:
    """生成路由前缀

    对于 ADMIN 路由，直接使用其定义的 prefix，不添加 controller_base_path。
    其他路由则拼接 controller_base_path 和 router_prefix。
    """
    # Admin 路由不需要 controller_base_path 前缀
    if router_info.router_prefix == RouterPrefix.ADMIN:
        return router_info.router_prefix

    # 其他路由正常拼接
    return f"{settings.controller_base_path}{router_info.router_prefix}"


def gen_api_router(router_info: RouterInfo, dependencies=None) -> APIRouter:
    router = APIRouter()
    router.tags = [router_info.router_tag]
    router.prefix = gen_router_prefix(router_info)
    # 设置router描述，用于API文档显示
    if router_info.description:
        router.__doc__ = router_info.description

    if dependencies:
        router.dependencies = dependencies
    return router


__all__ = ["gen_api_router"]
