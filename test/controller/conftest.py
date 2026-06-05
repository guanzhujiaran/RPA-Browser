"""
Controller 接口测试配置

使用 FastAPI TestClient 对 router 接口进行测试。
使用真实数据库（SQLite）和真实 CRUD 操作，不 mock CRUD 服务。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.controller.v1.browser_control.execution.action_router import router as action_router
from app.controller.v1.browser_control.execution.workflow_router import router as workflow_router
from app.controller.v1.browser_control.execution.plugin_router import router as plugin_router
from app.utils.depends.mid_depends import get_auth_info_from_header
from app.utils.depends.session_manager import DatabaseSessionManager


TEST_MID = 12345678


@pytest.fixture
def mock_auth():
    """Mock 认证信息"""
    from app.models.common.depends import AuthInfo

    return AuthInfo(mid=TEST_MID, level=5)


@pytest.fixture
def auth_headers(mock_auth):
    """生成认证 headers"""
    return {
        "x-bili-mid": str(mock_auth.mid),
        "x-bili-level": mock_auth.level,
    }


@pytest.fixture
def client(mock_auth):
    """创建 FastAPI TestClient，覆盖认证依赖"""
    app = FastAPI()
    app.include_router(action_router)
    app.include_router(workflow_router)
    app.include_router(plugin_router)

    # 覆盖认证依赖，使用 mock auth
    app.dependency_overrides[get_auth_info_from_header] = lambda: mock_auth

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
async def _cleanup_db():
    """每个测试前后清理数据库，避免跨测试数据污染"""
    yield
    # 测试后清理所有表
    from sqlmodel import select
    from app.models.database.workflow.models import (
        UserWorkflow, CompositeActionModel, UserPlugin,
    )

    async with DatabaseSessionManager.async_session() as session:
        for model in [UserPlugin, UserWorkflow, CompositeActionModel]:
            result = await session.exec(select(model))
            for obj in result.all():
                await session.delete(obj)
        await session.commit()
