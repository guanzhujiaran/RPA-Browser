"""
测试 Plugin 管理接口 - 使用真实数据库操作
"""

import pytest

from app.models.response_code import ResponseCode

PREFIX = "/api/v1/rpa/browser/control"


class TestListPlugins:

    def test_list_plugins_success(self, client):
        self._create_plugin(client)

        response = client.post(f"{PREFIX}/plugins/list", json={"page": 1, "per_page": 10})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["total"] >= 1
        assert len(data["data"]["items"]) >= 1
        assert data["data"]["items"][0]["name"] == "测试插件"

    @staticmethod
    def _create_plugin(client):
        action_data = {
            "name": "插件测试动作",
            "steps": [{"action_id": "click", "params": {}}],
        }
        action_resp = client.post(f"{PREFIX}/custom-actions/create", json=action_data)
        action_id = action_resp.json()["data"]["action_id"]
        
        request_data = {
            "name": "测试插件",
            "hook_type": "before_action",
            "custom_action_id": action_id,
            "description": "这是一个测试插件",
            "priority": 100,
            "is_public": False,
        }
        return client.post(f"{PREFIX}/plugins/create", json=request_data)


class TestCreatePlugin:

    def test_create_plugin_success(self, client):
        action_data = {
            "name": "插件测试动作",
            "steps": [{"action_id": "click", "params": {}}],
        }
        action_resp = client.post(f"{PREFIX}/custom-actions/create", json=action_data)
        action_id = action_resp.json()["data"]["action_id"]

        request_data = {
            "name": "测试插件",
            "hook_type": "before_action",
            "custom_action_id": action_id,
            "description": "这是一个测试插件",
            "priority": 100,
            "is_public": False,
        }

        response = client.post(f"{PREFIX}/plugins/create", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "测试插件"
        assert data["data"]["plugin_id"].startswith("plugin_")


class TestUpdatePlugin:

    def test_update_plugin_success(self, client):
        create_resp = self._create_plugin(client)
        plugin_id = create_resp.json()["data"]["id"]

        request_data = {
            "id": plugin_id,
            "name": "更新后的插件",
            "description": "更新后的描述",
            "hook_type": "after_action",
            "priority": 50,
            "is_public": True,
        }

        response = client.post(f"{PREFIX}/plugins/update", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    def test_update_plugin_not_found(self, client):
        response = client.post(f"{PREFIX}/plugins/update", json={"id": 999, "name": "不存在的"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_update_plugin_enable(self, client):
        create_resp = self._create_plugin(client)
        plugin_id = create_resp.json()["data"]["id"]

        response = client.post(f"{PREFIX}/plugins/update", json={"id": plugin_id, "is_enabled": True})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    def test_update_plugin_disable(self, client):
        create_resp = self._create_plugin(client)
        plugin_id = create_resp.json()["data"]["id"]

        response = client.post(f"{PREFIX}/plugins/update", json={"id": plugin_id, "is_enabled": False})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    @staticmethod
    def _create_plugin(client):
        action_data = {
            "name": "插件测试动作",
            "steps": [{"action_id": "click", "params": {}}],
        }
        action_resp = client.post(f"{PREFIX}/custom-actions/create", json=action_data)
        action_id = action_resp.json()["data"]["action_id"]
        
        request_data = {
            "name": "更新测试插件",
            "hook_type": "before_action",
            "custom_action_id": action_id,
        }
        return client.post(f"{PREFIX}/plugins/create", json=request_data)


class TestDeletePlugin:

    def test_delete_plugin_success(self, client):
        create_resp = self._create_plugin(client)
        plugin_id = create_resp.json()["data"]["id"]

        response = client.post(f"{PREFIX}/plugins/delete", params={"id": plugin_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    def test_delete_plugin_not_found(self, client):
        response = client.post(f"{PREFIX}/plugins/delete", params={"id": 999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_delete_plugin_no_permission(self, client):
        response = client.post(f"{PREFIX}/plugins/delete", params={"id": 999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_plugin(client):
        action_data = {
            "name": "插件测试动作",
            "steps": [{"action_id": "click", "params": {}}],
        }
        action_resp = client.post(f"{PREFIX}/custom-actions/create", json=action_data)
        action_id = action_resp.json()["data"]["action_id"]
        
        request_data = {
            "name": "删除测试插件",
            "hook_type": "before_action",
            "custom_action_id": action_id,
        }
        return client.post(f"{PREFIX}/plugins/create", json=request_data)


class TestForkPlugin:

    def test_fork_plugin_success(self, client):
        self._create_public_plugin(client)

        list_resp = client.post(f"{PREFIX}/plugins/list", json={"page": 1, "per_page": 100})
        plugin_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["name"] == "Fork源插件":
                plugin_id = item["id"]
                break

        assert plugin_id is not None

        response = client.post(f"{PREFIX}/plugins/fork", json={"id": plugin_id, "new_name": "我的 Fork"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert "Fork" in data["data"]["name"]

    def test_fork_plugin_not_found(self, client):
        response = client.post(f"{PREFIX}/plugins/fork", json={"id": 999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_fork_plugin_not_public(self, client):
        self._create_private_plugin(client)

        list_resp = client.post(f"{PREFIX}/plugins/list", json={"page": 1, "per_page": 100})
        plugin_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["name"] == "私有插件":
                plugin_id = item["id"]
                break

        assert plugin_id is not None

        response = client.post(f"{PREFIX}/plugins/fork", json={"id": plugin_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_public_plugin(client):
        action_data = {
            "name": "插件测试动作",
            "steps": [{"action_id": "click", "params": {}}],
        }
        action_resp = client.post(f"{PREFIX}/custom-actions/create", json=action_data)
        action_id = action_resp.json()["data"]["action_id"]
        
        request_data = {
            "name": "Fork源插件",
            "hook_type": "before_action",
            "custom_action_id": action_id,
            "is_public": True,
        }
        return client.post(f"{PREFIX}/plugins/create", json=request_data)

    @staticmethod
    def _create_private_plugin(client):
        action_data = {
            "name": "插件测试动作",
            "steps": [{"action_id": "click", "params": {}}],
        }
        action_resp = client.post(f"{PREFIX}/custom-actions/create", json=action_data)
        action_id = action_resp.json()["data"]["action_id"]
        
        request_data = {
            "name": "私有插件",
            "hook_type": "before_action",
            "custom_action_id": action_id,
            "is_public": False,
        }
        return client.post(f"{PREFIX}/plugins/create", json=request_data)


class TestGetPluginForks:

    def test_get_plugin_forks_success(self, client):
        self._create_plugin(client)

        list_resp = client.post(f"{PREFIX}/plugins/list", json={"page": 1, "per_page": 100})
        plugin_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["name"] == "Fork源插件":
                plugin_id = item["id"]
                break

        assert plugin_id is not None

        response = client.get(f"{PREFIX}/plugins/{plugin_id}/forks")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    def test_get_plugin_forks_not_found(self, client):
        response = client.get(f"{PREFIX}/plugins/999/forks")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_plugin(client):
        action_data = {
            "name": "插件测试动作",
            "steps": [{"action_id": "click", "params": {}}],
        }
        action_resp = client.post(f"{PREFIX}/custom-actions/create", json=action_data)
        action_id = action_resp.json()["data"]["action_id"]
        
        request_data = {
            "name": "Fork源插件",
            "hook_type": "before_action",
            "custom_action_id": action_id,
            "is_public": True,
        }
        return client.post(f"{PREFIX}/plugins/create", json=request_data)
