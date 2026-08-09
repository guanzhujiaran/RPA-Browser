"""RPA 服务中间件包"""

from app.utils.middlewares.ban_guard import BanGuardMiddleware

__all__ = ["BanGuardMiddleware"]
