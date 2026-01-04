from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
import uuid
from app.models.response import StandardResponse
from app.models.response_code import ResponseCode
import traceback
from app.config import settings
from sqlalchemy.exc import DisconnectionError, OperationalError
from app.models.exceptions.base_exception import BaseException as CustomBaseException

from loguru import logger


async def http_exception_handler(
    _: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """处理HTTP异常，如404等"""
    if exc.status_code == 404:
        response = StandardResponse(
            code=ResponseCode.NOT_FOUND, data=None, msg="API endpoint not found"
        )
    else:
        # 对于其他HTTP异常，也进行统一包装
        # 处理detail可能是字典的情况
        if isinstance(exc.detail, dict):
            msg = exc.detail.get("msg", "Error occurred")
            # 如果字典中有code字段，使用它；否则使用HTTP状态码
            code = exc.detail.get("code", exc.status_code)
            data = exc.detail.get("data", None)
        else:
            msg = exc.detail or "Error occurred"
            code = exc.status_code
            data = None

        response = StandardResponse(code=code, data=data, msg=msg)

    return JSONResponse(content=response.model_dump(), status_code=exc.status_code)


async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理请求验证异常"""
    logger.error(f"Request validation failed: {exc.errors()}")
    response = StandardResponse(
        code=ResponseCode.BAD_REQUEST,
        data=None,
        msg=f"请求参数验证失败: {exc.errors()}",
    )

    return JSONResponse(content=response.model_dump(), status_code=422)


async def custom_exception_handler(
    _: Request, exc: CustomBaseException
) -> JSONResponse:
    """处理自定义业务异常"""
    response = StandardResponse(
        code=exc.code,
        data=None,
        msg=exc.msg,
    )

    return JSONResponse(content=response.model_dump(), status_code=200)


async def global_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """处理所有未捕获的异常，特别优化数据库连接丢失处理"""

    # 生成错误ID用于追踪
    error_id = str(uuid.uuid4())

    # 检查是否为数据库连接丢失错误
    is_database_error = isinstance(exc, (DisconnectionError, OperationalError))
    is_connection_lost = is_database_error and (
        "Lost connection" in str(exc) or "MySQL server has gone away" in str(exc)
    )

    # 检查是否为自定义业务异常（有 code 和 msg 属性）
    exc_code = getattr(exc, "code", None)
    exc_msg = getattr(exc, "msg", None)
    is_custom_exception = exc_code is not None and exc_msg is not None

    # 获取详细的错误信息
    error_details = {
        "error_id": error_id,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": traceback.format_exc(),
        "is_database_error": is_database_error,
        "is_connection_lost": is_connection_lost,
    }
    status_code = 200

    # 根据错误类型设置不同的响应
    if is_connection_lost:
        # 数据库连接丢失，返回503而不是500
        status_code = 503
        error_message = "数据库连接丢失，请稍后重试"
        response_code = ResponseCode.SERVICE_UNAVAILABLE
        logger.warning(f"Database connection lost (ID: {error_id}): {exc}")
    elif is_custom_exception:
        # 自定义业务异常，使用异常中定义的 code 和 msg
        response_code = exc_code
        error_message = exc_msg
        logger.info(
            f"Custom exception (ID: {error_id}): {error_details['error_type']}: {error_message}"
        )
    else:
        # 未知异常
        response_code = ResponseCode.INTERNAL_ERROR
        error_message = (
            f"服务器内部错误 (错误ID: {error_id})\n{error_details['error_message']}"
        )
        logger.error(
            f"Unexpected error (ID: {error_id}): {error_details['error_type']}: {error_details['error_message']}"
        )

    # 根据settings中的环境配置输出错误信息
    if settings.environment.lower() == "development":
        # 自定义异常不需要打印详细的错误信息和 traceback
        if is_custom_exception:
            print(f"[Custom Exception] {error_details['error_type']}: {error_message}")
        else:
            print("\n" + "=" * 80)
            print("🚨 GLOBAL EXCEPTION HANDLER - DEVELOPMENT MODE 🚨")
            print("=" * 80)
            print(f"Error ID: {error_id}")
            print(f"Error Type: {error_details['error_type']}")
            print(f"Custom Exception: {is_custom_exception}")
            print(f"Database Error: {is_database_error}")
            print(f"Connection Lost: {is_connection_lost}")
            print(f"Error Message: {error_details['error_message']}")
            print("\nFull Traceback:")
            print(error_details["traceback"])
            print("=" * 80 + "\n")
    else:
        # 生产环境只记录简要信息
        log_level = logger.warning if is_connection_lost else logger.error
        log_level(
            f"Server Error (ID: {error_id}): {error_details['error_type']}: {error_details['error_message']}"
        )

    # 在开发环境下，将错误详情添加到data中（自定义异常除外）
    if settings.environment.lower() == "development" and not is_custom_exception:
        response_data = {
            "error_id": error_id,
            "error_type": error_details["error_type"],
            "error_message": error_details["error_message"],
            "is_database_error": is_database_error,
            "is_connection_lost": is_connection_lost,
        }
    else:
        response_data = None

    response = StandardResponse(
        code=response_code,
        data=response_data,
        msg=error_message,
    )

    return JSONResponse(content=response.model_dump(), status_code=status_code)


async def database_connection_handler(_: Request, exc: Exception) -> JSONResponse:
    """专门的数据库连接错误处理器"""
    error_id = str(uuid.uuid4())
    is_connection_lost = "Lost connection" in str(
        exc
    ) or "MySQL server has gone away" in str(exc)

    logger.warning(f"Database connection error (ID: {error_id}): {exc}")

    response_data = {
        "error_id": error_id,
        "error_type": type(exc).__name__,
        "is_connection_lost": is_connection_lost,
        "retry_after": 5,  # 建议客户端5秒后重试
    }

    response = StandardResponse(
        code=(
            ResponseCode.SERVICE_UNAVAILABLE
            if is_connection_lost
            else ResponseCode.INTERNAL_ERROR
        ),
        data=(
            response_data
            if settings.environment.lower() == "development"
            else {"error_id": error_id, "retry_after": 5}
        ),
        msg="数据库连接丢失，请稍后重试" if is_connection_lost else "数据库服务异常",
    )

    return JSONResponse(
        content=response.model_dump(), status_code=503  # Service Unavailable
    )
