"""
Controller 接口测试配置

使用 httpx.AsyncClient + ASGITransport 对 router 接口进行异步测试。
参考: https://fastapi.org.cn/advanced/async-tests/
使用真实数据库（SQLite）和真实 CRUD 操作，不 mock CRUD 服务。
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.controller.v1.browser_control.execution.action_router import (
    router as action_router,
)
from app.controller.v1.browser_control.execution.workflow_router import (
    router as workflow_router,
)
from app.controller.v1.browser_control.execution.execution_router import (
    router as execution_router,
)
from app.utils.depends.mid_depends import (
    get_auth_info_from_header,
    get_optional_auth_info_from_header,
)
from app.utils.depends.security_depends import verify_browser_ownership
from app.utils.depends.session_manager import DatabaseSessionManager
from bili_common.models.response_code import ResponseCode

PREFIX = "/api/v1/rpa/browser/control"
TEST_MID = 12345678
TEST_BROWSER_ID = 1
#: 另一个用户的 mid：用于「Fork 他人公开资源」这类需要跨用户语义的用例
OTHER_MID = 87654321


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
def mock_browser(mock_auth):
    """Mock 浏览器归属信息（执行类接口依赖 verify_browser_ownership）"""
    from bili_common.models.depends import BrowserReqAuthInfo

    return BrowserReqAuthInfo(browser_id=TEST_BROWSER_ID, auth_info=mock_auth)


@pytest.fixture
async def client(mock_auth, mock_browser):
    """创建 httpx.AsyncClient，覆盖认证 / 浏览器归属依赖"""
    app = FastAPI()
    app.include_router(action_router)
    app.include_router(workflow_router)
    app.include_router(execution_router)

    # 覆盖认证依赖，使用 mock auth
    app.dependency_overrides[get_auth_info_from_header] = lambda: mock_auth
    # 列表接口用可选登录依赖（游客可读公开内容），已登录场景下等价于 mock auth
    app.dependency_overrides[get_optional_auth_info_from_header] = lambda: mock_auth
    # 执行类接口用 Depends(verify_browser_ownership) 拿 mid/browser_id，
    # 测试不需要真实浏览器会话，直接注入固定的归属信息
    app.dependency_overrides[verify_browser_ownership] = lambda: mock_browser

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def anon_client():
    """未登录（游客）客户端：不做任何依赖覆盖，请求不带 x-bili-mid。

    用于验证「社区公开动作 / 公开工作流对游客可见」这条链路的真实鉴权行为：
    可选登录依赖返回 None，强制登录依赖仍抛 401。
    """
    app = FastAPI()
    app.include_router(action_router)
    app.include_router(workflow_router)

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
        CompositeActionModel,
        UserWorkflow,
        WorkflowRunRecord,
    )

    async with DatabaseSessionManager.async_session() as session:
        # 按依赖倒序删除：先删运行记录，再删工作流，最后删被引用的动作
        for model in [WorkflowRunRecord, UserWorkflow, CompositeActionModel]:
            await session.exec(delete(model))
        await session.commit()


# ═══════════════ 通用 Fixtures：消除各测试文件中的重复代码 ═══════════════


@pytest.fixture
async def created_action(client) -> int:
    """创建测试用自定义操作，返回雪花 action_id（int）

    注意：动作接口（get/update/delete/fork）一律以 `action_id` 定位，
    创建响应也不再返回数据库主键 `id`，这里只回传 action_id。
    """
    resp = await client.post(
        f"{PREFIX}/custom-actions/create",
        json={
            "name": "fixture_action",
            "steps": [{"action_id": "click", "params": {}}],
        },
    )
    data = resp.json()
    assert data["code"] == ResponseCode.SUCCESS
    return data["data"]["action_id"]


@pytest.fixture
async def created_public_action(client) -> int:
    """创建**他人**的公开自定义操作，返回雪花 action_id（int）

    为什么不用接口创建：接口创建的资源归属于当前登录用户，而 Fork 语义是
    「别人的公开资源才能复制」（自己的资源直接复用、不产生副本，
    fork 会返回空）。这里直接落一条 mid=OTHER_MID 的公开动作。
    """
    from app.models.database.workflow.models import BuiltinActionType
    from app.services.execution.crud_service import action_crud_svr

    model = await action_crud_svr.create(
        mid=OTHER_MID,
        name="public_fixture_action",
        action_type=BuiltinActionType.COMPOSITE,
        steps=[{"action_id": "click", "params": {}}],
        is_composite=True,
        is_public=True,
    )
    return model.action_id


@pytest.fixture
async def created_workflow(client) -> tuple[int, str]:
    """创建测试用工作流，返回 (db_id, workflow_id)"""
    resp = await client.post(
        f"{PREFIX}/workflows/create",
        json={
            "name": "fixture_workflow",
        },
    )
    data = resp.json()
    assert data["code"] == ResponseCode.SUCCESS
    return data["data"]["id"], data["data"]["workflow_id"]
