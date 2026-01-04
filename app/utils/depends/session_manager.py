from fastapi import Depends
from typing import Any, AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.exc import DisconnectionError, OperationalError
from sqlalchemy import text
from app.config import settings
import asyncio
from loguru import logger

# 数据库连接重试配置
MAX_RETRIES = 3
RETRY_DELAY = 1  # 秒

engine = create_async_engine(
    url=settings.mysql_browser_info_url,
    # 连接池配置 - 增强稳定性
    pool_size=20,  # 连接池中的连接数
    max_overflow=30,  # 超过pool_size后最多可以创建的连接数
    pool_pre_ping=True,  # 连接前检查连接是否有效
    pool_recycle=1800,  # 缩短连接回收时间至30分钟，防止MySQL断开空闲连接
    pool_timeout=30,  # 获取连接超时时间
    # 查询配置
    echo=False,  # 是否打印SQL语句，生产环境建议设为False
    future=True,  # 使用SQLAlchemy 2.0风格
    # 连接配置 - aiomysql 只支持少数参数
    connect_args={
        "charset": "utf8mb4",
        "autocommit": False,
        # aiomysql 只支持 charset 和 autocommit 参数
    },
)


class DatabaseSessionManager:
    @staticmethod
    def async_session():
        return AsyncSession(engine)

    """
    数据库会话管理器，用于创建和管理数据库会话
    增强了连接稳定性处理和重试机制
    """

    @staticmethod
    async def _safe_session_operation(session: AsyncSession, operation):
        """安全执行数据库操作，处理连接丢失"""
        for attempt in range(MAX_RETRIES):
            try:
                return await operation(session)
            except (DisconnectionError, OperationalError) as e:
                if "Lost connection" in str(e) or "MySQL server has gone away" in str(
                    e
                ):
                    if attempt < MAX_RETRIES - 1:
                        logger.warning(
                            f"Database connection lost, retrying ({attempt + 1}/{MAX_RETRIES}): {e}"
                        )
                        await asyncio.sleep(RETRY_DELAY * (attempt + 1))  # 指数退避
                        continue
                    else:
                        logger.error(
                            f"Database connection lost after {MAX_RETRIES} attempts: {e}"
                        )
                        raise
                else:
                    # 非连接丢失错误，直接抛出
                    raise
            except Exception:
                # 其他类型的错误，直接抛出
                raise

    @staticmethod
    async def get_db_session() -> AsyncGenerator[AsyncSession, Any]:
        """
        获取数据库会话，增强连接稳定性处理

        Yields:
            AsyncSession: 数据库会话对象
        """
        async with AsyncSession(engine) as session:
            try:
                yield session
            except (DisconnectionError, OperationalError) as e:
                if "Lost connection" in str(e) or "MySQL server has gone away" in str(
                    e
                ):
                    logger.error(f"Database connection lost during session: {e}")
                    # 对于连接丢失，session.close()可能会失败，所以安全处理
                    try:
                        await session.close()
                    except Exception as close_error:
                        logger.warning(
                            f"Error closing session after connection loss: {close_error}"
                        )
                    raise
                else:
                    # 其他数据库错误，正常回滚
                    try:
                        await session.rollback()
                    except Exception as rollback_error:
                        logger.warning(f"Error during rollback: {rollback_error}")
                    logger.error(f"Database session rolled back due to error: {e}")
                    raise
            except Exception as e:
                # 其他异常，尝试回滚
                try:
                    await session.rollback()
                except Exception as rollback_error:
                    logger.warning(f"Error during rollback: {rollback_error}")
                raise e
            finally:
                # 安全关闭会话
                try:
                    await session.close()
                except Exception as close_error:
                    if "Lost connection" in str(
                        close_error
                    ) or "MySQL server has gone away" in str(close_error):
                        logger.debug(
                            "Session close failed due to connection loss (expected)"
                        )
                    else:
                        logger.warning(
                            f"Unexpected error closing session: {close_error}"
                        )

    @staticmethod
    def get_dependency() -> AsyncGenerator[AsyncSession, Any]:
        """
        FastAPI依赖注入方法

        Yields:
            AsyncSession: 数据库会话对象
        """
        return Depends(DatabaseSessionManager.get_db_session)

    @staticmethod
    async def test_connection() -> bool:
        """测试数据库连接是否正常"""
        try:
            async with AsyncSession(engine) as session:
                await session.execute(text("SELECT 1"))
                return True
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False


__all__ = ["DatabaseSessionManager"]
