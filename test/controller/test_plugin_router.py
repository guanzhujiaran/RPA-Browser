"""
测试 Plugin 管理接口 - 使用真实数据库操作
使用 httpx.AsyncClient 进行异步测试
参考: https://fastapi.org.cn/advanced/async-tests/
"""

import pytest

from app.models.response_code import ResponseCode

# 复用 conftest 中的 PREFIX
from test.controller.conftest import PREFIX


@pytest.mark.anyio
class TestListPlugins:

    async def test_list_plugins_success(self, client, created_plugin):
        response = await client.post(f"{PREFIX}/plugins/list", json={"page": 1, "per_page": 10})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["total"] >= 1
        assert len(data["data"]["items"]) >= 1
        assert data["data"]["items"][0]["name"] == "fixture_plugin"


@pytest.mark.anyio
class TestCreatePlugin:

    async def test_create_plugin_success(self, client, created_action):
        _, action_id = created_action

        request_data = {
            "name": "测试插件",
            "hook_type": "before_action",
            "custom_action_id": action_id,
            "description": "这是一个测试插件",
            "priority": 100,
            "is_public": False,
        }

        response = await client.post(f"{PREFIX}/plugins/create", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "测试插件"
        assert data["data"]["plugin_id"].startswith("plugin_")


@pytest.mark.anyio
class TestUpdatePlugin:

    async def test_update_plugin_success(self, client, created_plugin):
        db_id, plugin_id = created_plugin

        request_data = {
            "id": db_id,
            "name": "更新后的插件",
            "description": "更新后的描述",
            "hook_type": "after_action",
            "priority": 50,
            "is_public": True,
        }

        response = await client.post(f"{PREFIX}/plugins/update", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_update_plugin_not_found(self, client):
        response = await client.post(f"{PREFIX}/plugins/update", json={"id": 999, "name": "不存在的"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"

    async def test_update_plugin_enable(self, client, created_plugin):
        db_id, plugin_id = created_plugin

        response = await client.post(f"{PREFIX}/plugins/update", json={"id": db_id, "is_enabled": True})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_update_plugin_disable(self, client, created_plugin):
        db_id, plugin_id = created_plugin

        response = await client.post(f"{PREFIX}/plugins/update", json={"id": db_id, "is_enabled": False})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS


@pytest.mark.anyio
class TestDeletePlugin:

    async def test_delete_plugin_success(self, client, created_plugin):
        db_id, plugin_id = created_plugin

        response = await client.post(f"{PREFIX}/plugins/delete", params={"id": db_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_delete_plugin_not_found(self, client):
        response = await client.post(f"{PREFIX}/plugins/delete", params={"id": 999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"

    async def test_delete_plugin_no_permission(self, client):
        """测试删除不存在的插件（权限检查前先返回 NOT_FOUND）"""
        response = await client.post(f"{PREFIX}/plugins/delete", params={"id": 999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"


@pytest.mark.anyio
class TestForkPlugin:

    async def test_fork_plugin_success(self, client, created_plugin):
        db_id, plugin_id = created_plugin

        # 先将插件设为公开
        await client.post(f"{PREFIX}/plugins/update", json={"id": db_id, "is_public": True})

        list_resp = await client.post(f"{PREFIX}/plugins/list", json={"page": 1, "per_page": 100})
        found_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["plugin_id"] == plugin_id:
                found_id = item["id"]
                break

        assert found_id is not None

        response = await client.post(f"{PREFIX}/plugins/fork", json={"id": found_id, "new_name": "我的 Fork"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert "Fork" in data["data"]["name"]

    async def test_fork_plugin_not_found(self, client):
        response = await client.post(f"{PREFIX}/plugins/fork", json={"id": 999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"

    async def test_fork_plugin_not_public(self, client, created_plugin):
        db_id, plugin_id = created_plugin
        # created_plugin 默认 is_public=False

        list_resp = await client.post(f"{PREFIX}/plugins/list", json={"page": 1, "per_page": 100})
        found_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["plugin_id"] == plugin_id:
                found_id = item["id"]
                break

        assert found_id is not None

        response = await client.post(f"{PREFIX}/plugins/fork", json={"id": found_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.FORBIDDEN, f"Expected FORBIDDEN, got {data['code']}: {data.get('message')}"


@pytest.mark.anyio
class TestGetPluginForks:

    async def test_get_plugin_forks_success(self, client, created_plugin):
        db_id, plugin_id = created_plugin

        response = await client.get(f"{PREFIX}/plugins/{db_id}/forks")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_get_plugin_forks_not_found(self, client):
        response = await client.get(f"{PREFIX}/plugins/999/forks")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"
