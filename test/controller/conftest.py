"""
Controller 接口测试配置

使用 FastAPI TestClient 对 router 接口进行测试。
使用真实数据库（SQLite）和真实 CRUD 操作，不 mock CRUD 服务。
"""
import pytest
from unittest.mock import MagicMock
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.controller.v1.browser_control.execution.action_router import router as action_router
from app.controller.v1.browser_control.execution.workflow_router import router as workflow_router
from app.controller.v1.browser_control.execution.plugin_router import router as plugin_router
from app.utils.depends.mid_depends import get_auth_info_from_header
from app.utils.depends import session_manager as sm_module


TEST_MID = 12345678
TEST_DB_URL = "sqlite+aiosqlite:///./test_controller.db"


@pytest.fixture
def mock_auth():
    """Mock 认证信息"""
    from app.models.common.depends import AuthInfo

    auth = MagicMock(spec=AuthInfo)
    auth.mid = TEST_MID
    auth.level = "level5"
    return auth


@pytest.fixture
def auth_headers(mock_auth):
    """生成认证 headers"""
    return {
        "x-bili-mid": str(mock_auth.mid),
        "x-bili-level": mock_auth.level,
    }


@pytest.fixture
def db_engine():
    """创建并清理数据库引擎"""
    import os
    # 删除已存在的数据库文件
    db_file = TEST_DB_URL.replace("sqlite+aiosqlite:///", "")
    if os.path.exists(db_file):
        os.remove(db_file)
    
    engine = create_async_engine(TEST_DB_URL, echo=False)
    
    import asyncio
    # 创建所有表
    async def _create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
    
    asyncio.get_event_loop().run_until_complete(_create_tables())
    
    yield engine
    
    # 清理
    async def _drop_tables():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
    
    asyncio.get_event_loop().run_until_complete(_drop_tables())
    
    asyncio.get_event_loop().run_until_complete(engine.dispose())
    
    # 删除数据库文件
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except:
            pass


@pytest.fixture
def client(mock_auth, auth_headers, db_engine):
    """创建测试客户端，使用真实 SQLite 数据库"""
    original_engine = sm_module.engine
    original_get_db = sm_module.DatabaseSessionManager.get_db_session

    sm_module.engine = db_engine

    @asynccontextmanager
    async def _test_get_db():
        async with AsyncSession(db_engine) as session:
            yield session

    sm_module.DatabaseSessionManager.get_db_session = staticmethod(_test_get_db)

    app = FastAPI()
    app.include_router(action_router)
    app.include_router(workflow_router)
    app.include_router(plugin_router)
    app.dependency_overrides[get_auth_info_from_header] = lambda: mock_auth

    with TestClient(app) as test_client:
        test_client.headers.update(auth_headers)
        yield test_client

    sm_module.engine = original_engine
    sm_module.DatabaseSessionManager.get_db_session = original_get_db
    app.dependency_overrides.clear()
