from fastapi import Depends
from typing import Any, AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from app.config import settings
import logging

logger = logging.getLogger(__name__)

engine = create_async_engine(
    url=settings.mysql_browser_info_url,
    # 连接池配置
    pool_size=20,  # 连接池中的连接数
    max_overflow=30,  # 超过pool_size后最多可以创建的连接数
    pool_pre_ping=True,  # 连接前检查连接是否有效
    pool_recycle=3600,  # 连接回收时间（秒），防止MySQL断开空闲连接
    # 查询配置
    echo=False,  # 是否打印SQL语句，生产环境建议设为False
    future=True,  # 使用SQLAlchemy 2.0风格
    # 连接配置
    connect_args={
        "charset": "utf8mb4",
        "autocommit": False,
    },
)


class DatabaseSessionManager:
    @staticmethod
    def async_session():
        return AsyncSession(engine)

    """
    数据库会话管理器，用于创建和管理数据库会话
    """

    @staticmethod
    async def get_db_session() -> AsyncGenerator[AsyncSession, Any]:
        """
        获取数据库会话

        Yields:
            AsyncSession: 数据库会话对象
        """
        async with AsyncSession(engine) as session:
            try:
                yield session
            except Exception as e:
                await session.rollback()
                logger.error(f"Database session rolled back due to error: {e}")
                raise
            finally:
                await session.close()

    @staticmethod
    def get_dependency() -> AsyncGenerator[AsyncSession, Any]:
        """
        FastAPI依赖注入方法

        Yields:
            AsyncSession: 数据库会话对象
        """
        return Depends(DatabaseSessionManager.get_db_session)


__all__ = ["DatabaseSessionManager"]
