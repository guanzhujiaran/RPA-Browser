from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
import uuid

from app.models.response import StandardResponse
from app.models.response_code import ResponseCode


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """处理HTTP异常，如404等"""
    if exc.status_code == 404:
        response = StandardResponse(
            code=ResponseCode.NOT_FOUND,
            data=None,
            msg="API endpoint not found"
        )
    else:
        # 对于其他HTTP异常，也进行统一包装
        response = StandardResponse(
            code=exc.status_code,
            data=None,
            msg=exc.detail or "Error occurred"
        )

    return JSONResponse(
        content=response.model_dump(),
        status_code=exc.status_code
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """处理请求验证异常"""
    response = StandardResponse(
        code=ResponseCode.BAD_REQUEST,
        data=None,
        msg=f"请求参数验证失败: {exc.errors()}"
    )
    
    return JSONResponse(
        content=response.model_dump(),
        status_code=422
    )


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理所有未捕获的异常"""
    # 记录异常日志
    import traceback
    traceback.print_exc()
    
    # 生成错误ID用于追踪
    error_id = str(uuid.uuid4())
    
    response = StandardResponse(
        code=ResponseCode.INTERNAL_ERROR,
        data=None,
        msg=f"服务器内部错误 (错误ID: {error_id})"
    )
    
    return JSONResponse(
        content=response.model_dump(),
        status_code=500
    )