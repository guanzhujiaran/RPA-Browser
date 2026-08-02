"""
Controller 接口测试配置

使用 httpx.AsyncClient + ASGITransport 对 router 接口进行异步测试。
参考: https://fastapi.org.cn/advanced/async-tests/
使用真实数据库（SQLite）和真实 CRUD 操作，不 mock CRUD 服务。
"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.controller.v1.browser_control.execution.action_router import router as action_router
from app.controller.v1.browser_control.execution.workflow_router import router as workflow_router
from app.controller.v1.browser_control.execution.plugin_router import router as plugin_router
from app.controller.v1.browser_control.execution.execution_router import router as execution_router
from app.utils.depends.mid_depends import get_auth_info_from_header
from app.utils.depends.session_manager import DatabaseSessionManager
from bili_common.models.response_code import ResponseCode

PREFIX = "/api/v1/rpa/browser/control"
TEST_MID = 12345678


# 覆盖 anyio_backend 为 session 级别，与父级 conftest 的 session fixture 兼容
@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_auth():
    """Mock 认证信息"""
    from bili_common.models.depends import AuthInfo

    return AuthInfo(mid=TEST_MID, level=5)


@pytest.fixture
def auth_headers(mock_auth):
    """生成认证 headers"""
    return {
        "x-bili-mid": str(mock_auth.mid),
        "x-bili-level": mock_auth.level,
    }


@pytest.fixture
async def client(mock_auth):
    """创建 httpx.AsyncClient，覆盖认证依赖"""
    app = FastAPI()
    app.include_router(action_router)
    app.include_router(workflow_router)
    app.include_router(plugin_router)
    app.include_router(execution_router)

    # 覆盖认证依赖，使用 mock auth
    app.dependency_overrides[get_auth_info_from_header] = lambda: mock_auth

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
async def _cleanup_db():
    """每个测试前后清理数据库，按外键依赖倒序删除避免约束冲突"""
    yield
    from sqlmodel import delete
    from app.models.database.workflow.models import (
        ResourceReport, ResourceLike,
        UserPlugin, UserWorkflow, CompositeActionModel,
    )

    async with DatabaseSessionManager.async_session() as session:
        # 按外键依赖倒序删除：先删子表（引用表），再删父表（被引用表）
        for model in [
            ResourceReport,          # 引用 ResourceLike 以外的各资源
            ResourceLike,            # 引用各资源
            UserPlugin,              # 引用 CompositeActionModel
            UserWorkflow,            # 引用 CompositeActionModel
            CompositeActionModel,    # 主表，被其他表引用
        ]:
            await session.exec(delete(model))
        await session.commit()


# ═══════════════ 通用 Fixtures：消除各测试文件中的重复代码 ═══════════════

@pytest.fixture
async def created_action(client) -> tuple[int, str]:
    """创建测试用自定义操作，返回 (db_id, action_id)"""
    resp = await client.post(f"{PREFIX}/custom-actions/create", json={
        "name": "fixture_action",
        "steps": [{"action_id": "click", "params": {}}],
    })
    data = resp.json()
    assert data["code"] == ResponseCode.SUCCESS
    return data["data"]["id"], data["data"]["action_id"]


@pytest.fixture
async def created_public_action(client) -> tuple[int, str]:
    """创建公开的自定义操作，返回 (db_id, action_id)"""
    resp = await client.post(f"{PREFIX}/custom-actions/create", json={
        "name": "public_fixture_action",
        "steps": [{"action_id": "click", "params": {}}],
        "is_public": True,
    })
    data = resp.json()
    assert data["code"] == ResponseCode.SUCCESS
    return data["data"]["id"], data["data"]["action_id"]


@pytest.fixture
async def created_workflow(client) -> tuple[int, str]:
    """创建测试用工作流，返回 (db_id, workflow_id)"""
    resp = await client.post(f"{PREFIX}/workflows/create", json={
        "name": "fixture_workflow",
    })
    data = resp.json()
    assert data["code"] == ResponseCode.SUCCESS
    return data["data"]["id"], data["data"]["workflow_id"]


@pytest.fixture
async def created_plugin(client) -> tuple[int, str]:
    """创建测试用插件，返回 (db_id, plugin_id)"""
    # 先创建一个 action 作为插件的关联操作
    action_resp = await client.post(f"{PREFIX}/custom-actions/create", json={
        "name": "plugin_fixture_action",
        "steps": [{"action_id": "click", "params": {}}],
    })
    action_id = action_resp.json()["data"]["action_id"]

    resp = await client.post(f"{PREFIX}/plugins/create", json={
        "name": "fixture_plugin",
        "hook_type": "before_action",
        "custom_action_id": action_id,
    })
    data = resp.json()
    assert data["code"] == ResponseCode.SUCCESS
    return data["data"]["id"], data["data"]["plugin_id"]