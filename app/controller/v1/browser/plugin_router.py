from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from typing import Union

from app.models.RPA_browser.plugin_model import (
    LogPluginModel,
    PageLimitPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel,
)

from app.controller.v1.browser.plugin_base import new_router
from app.models.response import StandardResponse, success_response
from app.services.RPA_browser.browser_service import BrowserService
from app.utils.depends.jwt_depends import get_browser_service

router = new_router()


@router.post(
    "/plugin/log/create",
    response_model=StandardResponse[LogPluginModel]
)
async def create_log_plugin_router(
        params: LogPluginModel,
        browser_service: BrowserService = Depends(get_browser_service),
        session: AsyncSession = Depends()
):
    """
    创建日志插件
    """
    plugin = await browser_service.create_log_plugin(
        params=params,
        session=session,
    )
    return success_response(data=plugin)


@router.post(
    "/plugin/page_limit/create",
    response_model=StandardResponse[PageLimitPluginModel]
)
async def create_page_limit_plugin_router(
        params: PageLimitPluginModel,
        browser_service: BrowserService = Depends(get_browser_service),
        session: AsyncSession = Depends()
):
    """
    创建页面限制插件
    """
    plugin = await browser_service.create_page_limit_plugin(
        params=params,
        session=session,
    )
    return success_response(data=plugin)


@router.post(
    "/plugin/random_wait/create",
    response_model=StandardResponse[RandomWaitPluginModel]
)
async def create_random_wait_plugin_router(
        params: RandomWaitPluginModel,
        browser_service: BrowserService = Depends(get_browser_service),
        session: AsyncSession = Depends()
):
    """
    创建随机等待插件
    """
    plugin = await browser_service.create_random_wait_plugin(
        params=params,
        session=session,
    )
    return success_response(data=plugin)


@router.post(
    "/plugin/retry/create",
    response_model=StandardResponse[RetryPluginModel]
)
async def create_retry_plugin_router(
        params: RetryPluginModel,
        browser_service: BrowserService = Depends(get_browser_service),
        session: AsyncSession = Depends()
):
    """
    创建重试插件
    """
    plugin = await browser_service.create_retry_plugin(
        params=params,
        session=session,
    )
    return success_response(data=plugin)


@router.post(
    "/plugin/delete",
    response_model=StandardResponse[bool]
)
async def delete_plugin_router(
        plugin_id: int,
        browser_service: BrowserService = Depends(get_browser_service),
        session: AsyncSession = Depends()
):
    """
    删除插件配置
    """
    result = await browser_service.delete_plugin(plugin_id, session)
    return success_response(data=result)
