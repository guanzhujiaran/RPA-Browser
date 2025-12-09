from fastapi import Header, Depends, HTTPException, status
from app.services.RPA_browser.browser_service import BrowserService
from app.utils.jwt_utils import verify_access_token


async def get_browser_service(x_bili_rpajwt: str = Header(..., alias="x-bili-rpajwt")) -> BrowserService:
    """
    从请求头中获取JWT令牌并创建BrowserService实例
    
    Args:
        x_bili_rpajwt: 从请求头中获取的JWT令牌 (格式: "Bearer <token>")
        
    Returns:
        BrowserService: 浏览器服务实例
        
    Raises:
        HTTPException: 当JWT令牌无效时抛出401错误
    """
    try:
        # 解析 "Bearer <token>" 格式
        if not x_bili_rpajwt.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="Invalid token format. Use 'Bearer <token>'",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        token = x_bili_rpajwt[7:]  # 移除 "Bearer " 前缀
        browser_token = verify_access_token(token)
        return BrowserService(browser_token)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )