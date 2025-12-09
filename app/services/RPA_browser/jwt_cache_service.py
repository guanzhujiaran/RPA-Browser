import uuid
from app.utils.jwt_utils import create_access_token

class JWTTokenService:
    """
    JWT令牌服务，用于生成JWT令牌
    注意：JWT是自包含的，不需要缓存
    """

    @staticmethod
    def generate_jwt_token(browser_token: uuid.UUID) -> str:
        """
        生成JWT令牌（不使用缓存）
        
        Args:
            browser_token: 浏览器令牌
            
        Returns:
            str: JWT令牌
        """
        # 直接创建新的JWT令牌
        return create_access_token(browser_token)
