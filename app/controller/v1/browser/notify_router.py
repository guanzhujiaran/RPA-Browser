from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from typing import Union
import uuid

from app.models.RPA_browser.notify_model import (
    NotificationConfig,
    NotificationConfigCreate,
    NotificationConfigUpdate
)

from app.controller.v1.browser.notify_base import new_router
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.services.RPA_browser.browser_service import BrowserService
from app.utils.depends.jwt_depends import get_browser_service


router = new_router()


@router.post(
    "/notify/create",
    response_model=StandardResponse[NotificationConfig]
)
async def create_notify_config_router(
        config: NotificationConfigCreate,
        browser_service: BrowserService = Depends(get_browser_service),
        session: AsyncSession = Depends()
):
    """
    创建推送通知配置
    """
    result = await browser_service.create_notification_config(config, session)
    return success_response(data=result)


@router.get(
    "/notify/read",
    response_model=StandardResponse[Union[NotificationConfig, None]]
)
async def read_notify_config_router(
        browser_service: BrowserService = Depends(get_browser_service),
        session: AsyncSession = Depends()
):
    """
    读取推送通知配置
    """
    result = await browser_service.get_notification_config(session)
    return success_response(data=result)


@router.post(
    "/notify/update",
    response_model=StandardResponse[Union[NotificationConfig, None]]
)
async def update_notify_config_router(
        config_update: NotificationConfigUpdate,
        browser_service: BrowserService = Depends(get_browser_service),
        session: AsyncSession = Depends()
):
    """
    更新推送通知配置
    """
    result = await browser_service.update_notification_config(config_update, session)
    return success_response(data=result)


@router.post(
    "/notify/delete",
    response_model=StandardResponse[bool]
)
async def delete_notify_config_router(
        browser_service: BrowserService = Depends(get_browser_service),
        session: AsyncSession = Depends()
):
    """
    删除推送通知配置
    """
    result = await browser_service.delete_notification_config(session)
    return success_response(data=result)