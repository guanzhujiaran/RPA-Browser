from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from app.utils.depends.mid_depends import AuthInfo

from app.models.RPA_browser.browser_info_model import (
    BaseFingerprintBrowserInitParams,
    BrowserFingerprintUpsertParams,
    BrowserFingerprintQueryParams,
    BrowserFingerprintDeleteParams,
    BrowserFingerprintCreateResp,
    BrowserFingerprintQueryResp,
    BrowserFingerprintDeleteResp,
    UserBrowserInfo,
    BrowserFingerprintListParams,
    BrowserFingerprintCreateParams
)
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.utils.depends.security_depends import verify_fingerprint_ownership, verify_fingerprint_limit
from app.models.base.base_sqlmodel import BasePaginationResp
from app.models.router.router_prefix import BrowserRouterPath
from app.utils.depends.session_manager import DatabaseSessionManager
from .base import new_router
from app.models.response import StandardResponse, success_response
from app.services.RPA_browser.browser_service import BrowserService
from app.services.RPA_browser.browser_db_service import BrowserDBService
from app.utils.depends.mid_depends import get_auth_info_from_header
from typing import Union

router = new_router()


@router.post(
    BrowserRouterPath.gen_rand_fingerprint,
    response_model=StandardResponse[BaseFingerprintBrowserInitParams],
    response_model_by_alias=False
)
async def gen_rand_fingerprint_router(params: BrowserFingerprintCreateParams):
    """
    生成随机浏览器指纹信息（不保存到数据库）

    生成一个随机的浏览器指纹配置，用于临时使用或测试。此接口不会将指纹信息保存到数据库中。
    支持自定义浏览器类型、操作系统、设备类型等参数，如果不指定则随机生成。

    Args:
        params: 浏览器指纹创建参数，包括浏览器类型、操作系统、设备类型等配置

    Returns:
        dict: 包含随机生成的浏览器指纹信息的字典，包括用户代理、屏幕分辨率、时区、语言等信息

    Note:
        此接口仅生成临时指纹，不会持久化存储。如需保存指纹，请使用 upsert_fingerprint 接口
    """
    # 为指纹生成创建一个临时的BrowserService实例
    temp_token = 0
    browser_service = BrowserService(temp_token)
    fingerprint = await browser_service.gen_rand_fingerprint(params)
    return success_response(data=fingerprint)


@router.post(
    BrowserRouterPath.upsert_fingerprint,
    response_model=StandardResponse[BrowserFingerprintCreateResp],
)
async def upsert_fingerprint_router(
    params: BrowserFingerprintUpsertParams,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
    _fingerprint_limit_check: AuthInfo = Depends(verify_fingerprint_limit),
):
    """
    创建或更新浏览器指纹信息 (upsert)

    如果提供了 id 则更新现有记录，否则创建新记录。
    创建时会生成一个随机的浏览器指纹配置并保存到数据库中，创建的指纹信息可以用于后续的浏览器实例启动。
    更新时会根据提供的 id 和需要更新的字段进行更新。
    支持自定义浏览器类型、操作系统、设备类型等参数，如果不指定则随机生成。

    Args:
        params: 浏览器指纹创建或更新参数，包括 id（可选）和各种浏览器配置
        auth_info: 认证信息，从请求头中自动获取
        session: 数据库会话

    Returns:
        BrowserFingerprintCreateResp: 创建或更新成功的浏览器指纹信息，包含数据库ID等

    Note:
        创建或更新的指纹信息会持久化保存，可用于浏览器实例的指纹配置
        更新操作时只能更新属于当前用户的浏览器指纹信息
        创建新指纹时会检查当前等级的指纹数量限制
    """
    result = await BrowserDBService.upsert_fingerprint(params, auth_info.mid, session)
    return success_response(data=result)


@router.post(
    BrowserRouterPath.read_fingerprint,
    response_model=StandardResponse[Union[BrowserFingerprintQueryResp, None]],
    response_model_by_alias=False
)
async def read_fingerprint_router(
    params: BrowserFingerprintQueryParams,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    读取指定条件的浏览器指纹信息

    根据提供的查询条件从数据库中读取浏览器指纹信息。支持按指纹ID、用户ID等多种条件查询。
    如果找到匹配的指纹信息则返回，否则返回None。

    Args:
        params: 浏览器指纹查询参数，包括指纹ID等查询条件
        auth_info: 认证信息，从请求头中自动获取
        session: 数据库会话

    Returns:
        Union[BrowserFingerprintQueryResp, None]: 找到的浏览器指纹信息，如果未找到则返回None

    Note:
        只能查询属于当前用户的浏览器指纹信息
    """
    result = await BrowserDBService.read_fingerprint(params, auth_info.mid, session)
    return success_response(data=result)






@router.post(
    BrowserRouterPath.delete_fingerprint,
    response_model=StandardResponse[BrowserFingerprintDeleteResp],
)
async def delete_fingerprint_router(
    browser_info: BrowserReqInfo = Depends(verify_fingerprint_ownership),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    删除指定的浏览器指纹信息

    根据提供的指纹ID从数据库中永久删除浏览器指纹配置。删除操作不可恢复，请谨慎操作。
    只能删除属于当前用户的浏览器指纹信息。

    Args:
        browser_info: 已验证的浏览器请求信息（通过依赖注入自动获取）
        session: 数据库会话

    Returns:
        BrowserFingerprintDeleteResp: 删除操作的结果，包含操作状态信息

    Note:
        删除操作不可恢复，请确保不再需要该指纹信息后再执行删除操作
    """
    await BrowserDBService.delete_fingerprint(
        BrowserFingerprintDeleteParams(id=browser_info.browser_id),
        browser_info.mid,
        session,
    )
    return success_response(
        data=BrowserFingerprintDeleteResp(
            id=browser_info.browser_id, mid=browser_info.mid, is_success=True
        ),
        msg="success",
    )


@router.post(BrowserRouterPath.count_fingerprint, response_model=StandardResponse[int])
async def count_fingerprint_router(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    统计当前用户的浏览器指纹数量

    获取当前用户在数据库中保存的浏览器指纹信息的总数量。用于显示指纹管理页面的统计信息。

    Args:
        auth_info: 认证信息，从请求头中自动获取
        session: 数据库会话

    Returns:
        int: 当前用户的浏览器指纹总数量

    Note:
        只统计属于当前用户的浏览器指纹信息
    """
    count = await BrowserDBService.count_fingerprint(auth_info.mid, session)
    return success_response(data=count)


@router.post(
    BrowserRouterPath.list_fingerprint,
    response_model=StandardResponse[BasePaginationResp[UserBrowserInfo]],
    response_model_by_alias=False
)
async def list_fingerprint_router(
    params: BrowserFingerprintListParams,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    分页列表显示当前用户的浏览器指纹信息

    以分页形式获取当前用户的浏览器指纹信息列表。支持分页参数配置，包括页码、每页数量等。
    返回的信息包括指纹ID、创建时间、浏览器类型、操作系统等基本信息。

    Args:
        params: 分页查询参数，包括页码、每页数量等
        auth_info: 认证信息，从请求头中自动获取
        session: 数据库会话

    Returns:
        BasePaginationResp[UserBrowserInfo]: 分页的浏览器指纹列表，包含总数、页码等信息

    Note:
        只返回属于当前用户的浏览器指纹信息，按创建时间倒序排列
    """
    result = await BrowserDBService.list_fingerprint(params, auth_info.mid, session)
    return success_response(data=result)
