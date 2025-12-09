from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.RPA_browser.browser_info_model import (
    BaseFingerprintBrowserInitParams,
    UserBrowserInfoCreateParams,
    UserBrowserInfoReadParams,
    UserBrowserInfoUpdateParams,
    UserBrowserInfoDeleteParams,
    UserBrowserInfoCreateResp,
    UserBrowserInfoReadResp,
    UserBrowserInfoUpdateResp,
    UserBrowserInfoDeleteResp, UserBrowserInfoCountParams, UserBrowserInfoListParams
)
from app.models.base.base_sqlmodel import BasePaginationResp
from app.models.router.router_prefix import BrowserRouterPath
from app.utils.depends.session_manager import DatabaseSessionManager
from .base import new_router
from app.models.response import StandardResponse, success_response, custom_response
from app.services.RPA_browser.browser_service import BrowserService
from app.services.RPA_browser.browser_db_service import BrowserDBService
from app.utils.depends.jwt_depends import get_browser_service
from typing import Union
import uuid

router = new_router()


@router.post(
    BrowserRouterPath.gen_rand_fingerprint,
    response_model=StandardResponse[BaseFingerprintBrowserInitParams]
)
def gen_rand_fingerprint_router(
        params: UserBrowserInfoCreateParams
):
    """
    生成随机的浏览器指纹信息

    Returns:
        dict: 包含随机生成的浏览器指纹信息的字典，具体字段由底层 gen_from_browserforge_fingerprint() 函数决定
    """
    # 为指纹生成创建一个临时的BrowserService实例
    temp_token = uuid.uuid4()
    browser_service = BrowserService(temp_token)
    fingerprint = browser_service.gen_rand_fingerprint(params)
    return success_response(data=fingerprint)


@router.post(
    BrowserRouterPath.create_fingerprint,
    response_model=StandardResponse[UserBrowserInfoCreateResp]
)
async def create_fingerprint_router(
        params: UserBrowserInfoCreateParams,
        session: AsyncSession = Depends(DatabaseSessionManager.get_db_session)
):
    """
    生成随机的浏览器指纹信息
    """
    result = await BrowserDBService.create_fingerprint(params, session)
    return success_response(data=result)


@router.post(
    BrowserRouterPath.read_fingerprint,
    response_model=StandardResponse[Union[UserBrowserInfoReadResp, None]]
)
async def read_fingerprint_router(
        params: UserBrowserInfoReadParams,
        session: AsyncSession = Depends(DatabaseSessionManager.get_db_session)
):
    """
    读取浏览器指纹信息
    """
    result = await BrowserDBService.read_fingerprint(params, session)
    return success_response(data=result)


@router.post(
    BrowserRouterPath.update_fingerprint,
    response_model=StandardResponse[UserBrowserInfoUpdateResp]
)
async def update_fingerprint_router(
        params: UserBrowserInfoUpdateParams,
        session: AsyncSession = Depends(DatabaseSessionManager.get_db_session)
):
    """
    更新浏览器指纹信息
    """
    code, is_success, msg = await BrowserDBService.update_fingerprint(params, session)
    return custom_response(code=code, data=UserBrowserInfoUpdateResp(
        browser_token=params.browser_token,
        is_success=is_success
    ), msg=msg)


@router.post(
    BrowserRouterPath.delete_fingerprint,
    response_model=StandardResponse[UserBrowserInfoDeleteResp]
)
async def delete_fingerprint_router(
        params: UserBrowserInfoDeleteParams,
        session: AsyncSession = Depends(DatabaseSessionManager.get_db_session)
):
    """
    删除浏览器指纹信息
    """
    code, is_success, msg = await BrowserDBService.delete_fingerprint(params, session)
    return custom_response(
        code=code,
        data=UserBrowserInfoDeleteResp(
            browser_token=params.browser_token,
            is_success=is_success
        ), msg=msg)


@router.post(
    BrowserRouterPath.count_fingerprint,
    response_model=StandardResponse[int]
)
async def count_fingerprint_router(
        params: UserBrowserInfoCountParams,
        session: AsyncSession = Depends(DatabaseSessionManager.get_db_session)
):
    """
    统计浏览器指纹信息
    """
    count = await BrowserDBService.count_fingerprint(params, session)
    return success_response(data=count)


@router.post(
    BrowserRouterPath.list_fingerprint,
    response_model=StandardResponse[BasePaginationResp[UserBrowserInfoReadResp]]
)
async def list_fingerprint_router(
        params: UserBrowserInfoListParams,
        session: AsyncSession = Depends(DatabaseSessionManager.get_db_session)
):
    """
    列表浏览器指纹信息
    """
    result = await BrowserDBService.list_fingerprint(
        params,
        session
    )
    return success_response(data=result)