"""封禁拦截中间件

被封禁用户（rpa_user_ban 中存在生效中的记录）访问 RPA 服务的任何业务接口时直接返回
403 标准响应，不进入业务逻辑。

说明：
- 身份取自网关注入的 `x-bili-mid` / `x-bili-role` 请求头；root 不受封禁影响。
- 命中判定走带 TTL 的内存缓存（默认 30s），封禁/解封操作会主动失效缓存。
- 当前封禁仅在 RPA 服务生效，不影响评论/私信服务。
"""

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from bili_common.models.response import StandardResponse
from bili_common.models.response_code import ResponseCode

from app.services.user_ban import get_active_ban, is_user_banned

# 无需鉴权/无需封禁校验的路径前缀
_WHITELIST_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/health",
)


class BanGuardMiddleware(BaseHTTPMiddleware):
    """拦截被封禁用户的请求"""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith(_WHITELIST_PREFIXES):
            return await call_next(request)

        role = request.headers.get("x-bili-role", "normal")
        if role == "root":
            return await call_next(request)

        mid_raw = request.headers.get("x-bili-mid")
        if not mid_raw:
            return await call_next(request)
        try:
            mid = int(mid_raw)
        except (TypeError, ValueError):
            return await call_next(request)

        try:
            if not await is_user_banned(mid):
                return await call_next(request)
        except Exception as e:  # 封禁查询异常不应阻断正常业务
            logger.warning(f"⚠️ 封禁状态校验失败，放行请求: mid={mid}, err={e}")
            return await call_next(request)

        ban = await get_active_ban(mid)
        expired_desc = (
            "永久封禁"
            if ban is None or ban.expired_at is None
            else f"封禁至 {ban.expired_at:%Y-%m-%d %H:%M:%S}"
        )
        reason = (ban.reason if ban else "") or "违反平台规则"
        logger.info(f"🚫 拦截被封禁用户请求: mid={mid} path={path}")
        response = StandardResponse(
            code=ResponseCode.FORBIDDEN,
            data=None,
            msg=f"账号已被封禁（{expired_desc}），原因：{reason}",
        )
        return JSONResponse(content=response.model_dump(), status_code=200)


__all__ = ["BanGuardMiddleware"]
