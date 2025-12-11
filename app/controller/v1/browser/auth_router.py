from fastapi import Depends
import uuid
from app.controller.v1.browser.base import new_router
from app.models.response import StandardResponse, success_response
from app.models.RPA_browser.browser_info_model import (
    UserBrowserInfoCreateParams,
    UserBrowserInfoReadParams,
    UserBrowserInfoReadResp
)
from app.services.RPA_browser.browser_db_service import BrowserDBService
from app.services.RPA_browser.browser_service import BrowserService
from app.services.RPA_browser.jwt_cache_service import JWTTokenService
from app.utils.depends.session_manager import DatabaseSessionManager
from sqlmodel.ext.asyncio.session import AsyncSession

router = new_router()


@router.post("/auth/token", response_model=StandardResponse[dict])
async def generate_token_router(
        params: UserBrowserInfoCreateParams,
        session: AsyncSession = DatabaseSessionManager.get_dependency()
):
    """
    生成浏览器指纹和对应的JWT访问令牌
    
    Returns:
        dict: 包含browser_token和access_token的字典
    """
    # 创建浏览器指纹
    fingerprint_result = await BrowserDBService.create_fingerprint(params, session)
    
    # 生成JWT访问令牌
    access_token = JWTTokenService.generate_jwt_token(fingerprint_result.browser_token)
    
    return success_response(data={
        "browser_token": str(fingerprint_result.browser_token),
        "access_token": access_token,
        "token_type": "bearer"
    })


@router.post("/auth/issue-jwt", response_model=StandardResponse[dict])
async def issue_jwt_token_router(
        params: UserBrowserInfoReadParams,
        session: AsyncSession = DatabaseSessionManager.get_dependency()
):
    """
    根据已有的browser_token下发JWT访问令牌
    
    如果存在未过期的JWT令牌，则返回它；
    否则创建一个新的JWT令牌。
    
    参数:
        params: 包含browser_token和id的参数对象
        
    Returns:
        dict: 包含access_token的字典
    """
    # 读取浏览器指纹信息以验证browser_token是否存在
    fingerprint_result = await BrowserDBService.read_fingerprint(params, session)
    
    if not fingerprint_result:
        return success_response(data={
            "access_token": None,
            "token_type": "bearer"
        }, msg="browser_token not found")
    
    # 获取或创建JWT访问令牌（使用缓存避免重复创建）
    access_token = JWTokenCacheService.get_or_create_jwt_token(params.browser_token)
    
    return success_response(data={
        "access_token": access_token,
        "token_type": "bearer"
    })