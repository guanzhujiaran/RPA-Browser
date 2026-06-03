"""
执行引擎测试专用配置
"""
import pytest
import asyncio
import os
from typing import AsyncGenerator

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


TEST_DB_URL = "sqlite+aiosqlite:///./test_execution_engine.db"

@pytest.fixture(scope="function")
async def db_engine():
    """创建测试数据库引擎，每个函数运行清理"""
    # 删除已存在的数据库文件
    db_file = TEST_DB_URL.replace("sqlite+aiosqlite:///", "")
    if os.path.exists(db_file):
        os.remove(db_file)
    
    engine = create_async_engine(
        TEST_DB_URL,
        echo=False,
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    
    yield engine
    
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    
    await engine.dispose()
    
    # 删除数据库文件
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except:
            pass


@pytest.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """创建测试数据库会话"""
    async_session = sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session() as session:
        yield session
        await session.rollback()
