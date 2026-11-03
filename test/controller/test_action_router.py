"""Action 管理接口测试 - 使用真实数据库操作

契约要点（与 app/controller/.../action_router.py 对齐）：
- 动作主键统一为雪花 ``action_id``（int）；get/update/delete/fork 均以它定位，
  创建响应也不再返回数据库自增主键 ``id``；
- 「资源不存在」走业务码 ``ACTION_NOT_FOUND``（HTTP 仍 200），不再用 404；
- Fork 端点路径是 ``/custom_actions/fork``（下划线，历史路径）。
"""

import pytest

from bili_common.models.response_code import ResponseCode

# 复用 conftest 中的 PREFIX
from test.controller.conftest import PREFIX


@pytest.mark.anyio
class TestListRegisteredActions:
    async def test_list_registered_actions_success(self, client):
        response = await client.post(f"{PREFIX}/actions/registered")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert len(data["data"]) > 0


@pytest.mark.anyio
class TestCreateCompositeAction:
    async def test_create_custom_action_success(self, client):
        request_data = {
            "name": "测试操作",
            "description": "这是一个测试操作",
            "steps": [{"action_id": "click", "params": {"selector": "#button"}}],
            "tags": ["test"],
            "input_vars": [{"name": "url", "type": "string", "required": True}],
            "output_vars": ["result"],
            "timeout": 60000,
            "retry_on_error": True,
            "retry_times": 3,
            "retry_delay": 2.0,
        }

        response = await client.post(
            f"{PREFIX}/custom-actions/create", json=request_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "测试操作"
        # action_id 已是雪花 int，不再有 "ca_" 前缀
        assert isinstance(data["data"]["action_id"], int)
        assert data["data"]["action_id"] > 0
        assert data["data"]["timeout"] == 60000
        assert data["data"]["retry_on_error"] is True
        assert data["data"]["retry_times"] == 3
        assert data["data"]["retry_delay"] == 2.0

    async def test_create_custom_action_minimal(self, client):
        request_data = {"name": "最小化操作"}

        response = await client.post(
            f"{PREFIX}/custom-actions/create", json=request_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "最小化操作"


@pytest.mark.anyio
class TestListCompositeActions:
    async def test_list_custom_actions_after_create(self, client, created_action):
        response = await client.post(
            f"{PREFIX}/custom-actions/list", json={"page": 1, "per_page": 10}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["total"] >= 1
        assert len(data["data"]["items"]) >= 1


@pytest.mark.anyio
class TestAnonymousCommunityActions:
    """未登录用户（社区广场游客）只能看到公开动作"""

    async def test_community_list_returns_public_actions(
        self, anon_client, created_public_action
    ):
        response = await anon_client.post(
            f"{PREFIX}/custom-actions/list",
            json={"page": 1, "per_page": 10, "filter_type": "community"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        action_ids = [item["action_id"] for item in data["data"]["items"]]
        assert created_public_action in action_ids

    async def test_all_list_excludes_private_actions_for_anonymous(
        self, anon_client, created_action, created_public_action
    ):
        response = await anon_client.post(
            f"{PREFIX}/custom-actions/list",
            json={"page": 1, "per_page": 10, "filter_type": "all"},
        )

        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        action_ids = [item["action_id"] for item in data["data"]["items"]]
        assert created_public_action in action_ids
        # created_action 是登录用户的私有动作，游客不应看到
        assert created_action not in action_ids
        assert all(item["is_public"] for item in data["data"]["items"])

    async def test_private_list_is_empty_for_anonymous(
        self, anon_client, created_action
    ):
        response = await anon_client.post(
            f"{PREFIX}/custom-actions/list",
            json={"page": 1, "per_page": 10, "filter_type": "private"},
        )

        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["total"] == 0
        assert data["data"]["items"] == []


@pytest.mark.anyio
class TestGetCompositeAction:
    async def test_get_custom_action_after_create(self, client, created_action):
        action_id = created_action

        response = await client.post(
            f"{PREFIX}/custom-actions/get", json={"action_id": action_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "fixture_action"
        assert data["data"]["action_id"] == action_id
        assert data["data"]["input_vars"] == []
        assert data["data"]["output_vars"] == []

    async def test_get_custom_action_not_found(self, client):
        response = await client.post(
            f"{PREFIX}/custom-actions/get", json={"action_id": 999999}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.ACTION_NOT_FOUND, (
            f"Expected ACTION_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )


@pytest.mark.anyio
class TestUpdateCompositeAction:
    async def test_update_custom_action_success(self, client, created_action):
        action_id = created_action

        request_data = {
            "action_id": action_id,
            "name": "更新后的操作",
            "description": "更新后的描述",
            "steps": [{"action_id": "input", "params": {"value": "hello"}}],
            "tags": ["updated"],
            "input_vars": [{"name": "text", "type": "string"}],
            "output_vars": ["result"],
            "timeout": 60000,
            "retry_on_error": True,
            "retry_times": 3,
            "retry_delay": 2.0,
        }

        response = await client.post(
            f"{PREFIX}/custom-actions/update", json=request_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "更新后的操作"
        assert data["data"]["timeout"] == 60000
        assert data["data"]["retry_on_error"] is True
        assert data["data"]["retry_times"] == 3
        assert data["data"]["retry_delay"] == 2.0

    async def test_update_custom_action_not_found(self, client):
        response = await client.post(
            f"{PREFIX}/custom-actions/update",
            json={"action_id": 999999, "name": "不存在的操作"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.ACTION_NOT_FOUND, (
            f"Expected ACTION_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )


@pytest.mark.anyio
class TestDeleteCompositeAction:
    async def test_delete_custom_action_success(self, client, created_action):
        action_id = created_action

        response = await client.post(
            f"{PREFIX}/custom-actions/delete", json={"action_id": action_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_delete_custom_action_not_found(self, client):
        response = await client.post(
            f"{PREFIX}/custom-actions/delete", json={"action_id": 999999}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.ACTION_NOT_FOUND, (
            f"Expected ACTION_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )


@pytest.mark.anyio
class TestForkCompositeAction:
    async def test_fork_custom_action_success(self, client, created_public_action):
        action_id = created_public_action

        response = await client.post(
            f"{PREFIX}/custom_actions/fork",
            json={"action_id": action_id, "new_name": "我的 Fork"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert "Fork" in data["data"]["name"]

    async def test_fork_custom_action_not_found(self, client):
        response = await client.post(
            f"{PREFIX}/custom_actions/fork", json={"action_id": 999999}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.ACTION_NOT_FOUND, (
            f"Expected ACTION_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )
