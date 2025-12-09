from typing import Any, AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from app.config import settings

engine = create_async_engine(url=settings.mysql_browser_info_url)


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
            yield session


__all__ = ["DatabaseSessionManager"]
