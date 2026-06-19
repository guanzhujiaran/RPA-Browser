"""
社区互动接口测试

测试端点：社区列表、点赞、举报、Fork、举报更新
通过 API 创建测试数据（避免直接数据库操作导致的 MissingGreenlet 问题）
"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.controller.v1.browser_control.execution.community_router import router as community_router
from app.controller.v1.browser_control.execution.action_router import router as action_router
from app.utils.depends.mid_depends import get_auth_info_from_header
from app.utils.depends.session_manager import DatabaseSessionManager

PREFIX = "/api/v1/rpa/browser/control"
TEST_MID = "12345678"
OTHER_MID = "87654321"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_auth():
    from app.models.common.depends import AuthInfo
    return AuthInfo(mid=TEST_MID, level=5)


def _create_app(auth_override):
    """创建测试用 FastAPI app"""
    app = FastAPI()
    app.include_router(community_router)
    app.include_router(action_router)
    app.dependency_overrides[get_auth_info_from_header] = auth_override
    return app


@pytest.fixture
async def client(mock_auth):
    """主测试客户端（TEST_MID），使用默认 mock auth"""
    app = _create_app(lambda: mock_auth)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def other_client():
    """其他用户测试客户端（OTHER_MID）"""
    from app.models.common.depends import AuthInfo
    other_auth = AuthInfo(mid=OTHER_MID, level=5)
    app = _create_app(lambda: other_auth)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


async def _create_public_action(client: AsyncClient, name="公开操作") -> tuple[str, int]:
    """通过 API 创建公开操作，返回 (action_id, db_id)"""
    resp = await client.post(f"{PREFIX}/custom-actions/create", json={
        "name": name,
        "is_public": True,
        "steps": [{"action_id": "click", "action_type": "click", "params": {"selector": "#btn"}}],
    })
    data = resp.json()
    return data["data"]["action_id"], data["data"]["id"]


async def _create_public_workflow(client: AsyncClient, action_id: str, name="公开工作流") -> tuple[str, int]:
    """通过 API 创建公开工作流，返回 (workflow_id, db_id)"""
    resp = await client.post(f"{PREFIX}/workflows/create", json={
        "name": name,
        "custom_action_id": action_id,
        "is_public": True,
    })
    data = resp.json()
    return data["data"]["workflow_id"], data["data"]["id"]


async def _create_public_plugin(client: AsyncClient, custom_action_id: str, name="公开插件") -> tuple[str, int]:
    """通过 API 创建公开插件，返回 (plugin_id, db_id)"""
    resp = await client.post(f"{PREFIX}/plugins/create", json={
        "name": name,
        "custom_action_id": custom_action_id,
        "hook_type": "after_action",
        "is_public": True,
    })
    data = resp.json()
    return data["data"]["plugin_id"], data["data"]["id"]


@pytest.fixture(autouse=True)
def _cleanup_db():
    """每个测试前后清理数据库（同步版本，避免异步 fixture 时序问题）"""
    yield
    import anyio
    from sqlmodel import delete
    from app.models.database.workflow.models import (
        ResourceReport, ResourceLike, UserPlugin, UserWorkflow, CompositeActionModel,
    )

    async def _cleanup():
        async with DatabaseSessionManager.async_session() as session:
            for model in [ResourceReport, ResourceLike, UserPlugin, UserWorkflow, CompositeActionModel]:
                await session.exec(delete(model))
            await session.commit()

    anyio.run(_cleanup)


class TestCommunityListEndpoints:
    """社区公开列表接口测试"""

    @pytest.mark.anyio
    async def test_list_community_actions(self, client: AsyncClient, other_client: AsyncClient):
        """测试获取社区公开操作列表"""
        await _create_public_action(other_client, "公开操作A")
        await _create_public_action(other_client, "公开操作B")

        response = await client.post(f"{PREFIX}/community/actions/list", json={
            "page": 1, "per_page": 10, "filter_type": "community",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert len(data["data"]["items"]) >= 2

    @pytest.mark.anyio
    async def test_list_community_actions_empty(self, client: AsyncClient):
        """测试社区公开操作为空"""
        response = await client.post(f"{PREFIX}/community/actions/list", json={
            "page": 1, "per_page": 10, "filter_type": "community",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestCommunityLikeEndpoints:
    """点赞/取消点赞接口测试"""

    @pytest.mark.anyio
    async def test_like_action(self, client: AsyncClient, other_client: AsyncClient):
        """测试点赞自定义操作（路由 action_id 参数为数据库整数 id）"""
        _, db_id = await _create_public_action(other_client, "点赞操作")

        response = await client.post(f"{PREFIX}/community/action/{db_id}/like")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    @pytest.mark.anyio
    async def test_like_action_toggle(self, client: AsyncClient, other_client: AsyncClient):
        """测试点赞取消点赞（toggle）"""
        from app.services.execution.crud_service.community_crud import community_crud_svr
        from app.models.database.workflow.models import ResourceType

        _, db_id = await _create_public_action(other_client, "Toggle操作")
        print(f"DB ID: {db_id}")

        # 直接用 service 调用 toggle_like
        result1 = await community_crud_svr.toggle_like(
            mid=TEST_MID,
            resource_type=ResourceType.CUSTOM_ACTION,
            resource_id=db_id,
        )
        print(f"Toggle 1 result: {result1}")
        assert result1 is True

        result2 = await community_crud_svr.toggle_like(
            mid=TEST_MID,
            resource_type=ResourceType.CUSTOM_ACTION,
            resource_id=db_id,
        )
        print(f"Toggle 2 result: {result2}")
        assert result2 is False


class TestCommunityReportEndpoints:
    """举报接口测试"""

    @pytest.mark.anyio
    async def test_report_action(self, client: AsyncClient, other_client: AsyncClient):
        """测试举报自定义操作"""
        _, db_id = await _create_public_action(other_client, "举报操作")

        response = await client.post(
            f"{PREFIX}/community/action/{db_id}/report",
            json={"reason": 1, "description": "垃圾内容"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    @pytest.mark.anyio
    async def test_report_with_default_reason(self, client: AsyncClient, other_client: AsyncClient):
        """测试不传 reason 时使用默认值举报"""
        _, db_id = await _create_public_action(other_client, "默认理由操作")

        response = await client.post(
            f"{PREFIX}/community/action/{db_id}/report",
            json={"description": "垃圾内容"},
        )

        # ReportRequest.reason 有默认值 5，所以应该成功
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    @pytest.mark.anyio
    async def test_report_missing_description(self, client: AsyncClient, other_client: AsyncClient):
        """测试不传 description 时举报"""
        _, db_id = await _create_public_action(other_client, "无描述操作")

        response = await client.post(
            f"{PREFIX}/community/action/{db_id}/report",
            json={"reason": 2},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestCommunityForkEndpoints:
    """Fork 接口测试"""

    @pytest.mark.anyio
    async def test_fork_action(self, client: AsyncClient, other_client: AsyncClient):
        """测试 Fork 公开操作"""
        _, action_db_id = await _create_public_action(other_client, "Fork操作")

        response = await client.post(
            f"{PREFIX}/community/actions/fork",
            json={"id": action_db_id, "new_name": "我的 Fork 操作"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    @pytest.mark.anyio
    async def test_fork_nonexistent_action(self, client: AsyncClient):
        """测试 Fork 不存在的操作"""
        response = await client.post(
            f"{PREFIX}/community/actions/fork",
            json={"id": 999999, "new_name": "不存在的 Fork"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != 0


class TestCommunityReportUpdate:
    """举报更新接口测试"""

    @pytest.mark.anyio
    async def test_report_update(self, client: AsyncClient, other_client: AsyncClient):
        """测试更新举报内容"""
        _, action_db_id = await _create_public_action(other_client, "更新举报操作")

        # 先创建举报
        await client.post(
            f"{PREFIX}/community/action/{action_db_id}/report",
            json={"reason": 1, "description": "初始描述"},
        )

        # 从数据库中获取举报记录ID
        from sqlmodel import select
        from app.models.database.workflow.models import ResourceReport
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(ResourceReport).where(
                    ResourceReport.resource_id == action_db_id
                )
            )
            report = result.first()
            assert report is not None
            report_id = report.id

        # 更新举报
        response = await client.post(
            f"{PREFIX}/community/report/update",
            json={
                "report_id": report_id,
                "reason": 3,
                "description": "更新后的描述",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0