"""
测试 Action 管理接口 - 使用真实数据库操作
"""
import pytest

from app.models.response_code import ResponseCode

PREFIX = "/api/v1/rpa/browser/control"


class TestListRegisteredActions:

    def test_list_registered_actions_success(self, client):
        response = client.post(f"{PREFIX}/actions/registered")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert len(data["data"]) > 0


class TestCreateCompositeAction:

    def test_create_custom_action_success(self, client):
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
            "enabled_plugins": [],
        }

        response = client.post(f"{PREFIX}/custom-actions/create", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "测试操作"
        assert data["data"]["action_id"].startswith("ca_")
        assert data["data"]["timeout"] == 60000
        assert data["data"]["retry_on_error"] is True
        assert data["data"]["retry_times"] == 3
        assert data["data"]["retry_delay"] == 2.0
        return data["data"]

    def test_create_custom_action_minimal(self, client):
        request_data = {"name": "最小化操作"}

        response = client.post(f"{PREFIX}/custom-actions/create", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "最小化操作"
        return data["data"]


class TestListCompositeActions:

    def test_list_custom_actions_after_create(self, client):
        self._create_action(client)

        response = client.post(f"{PREFIX}/custom-actions/list", json={"page": 1, "per_page": 10})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["total"] >= 1
        assert len(data["data"]["items"]) >= 1
        assert data["data"]["items"][0]["name"] == "列表测试操作"

    @staticmethod
    def _create_action(client):
        request_data = {
            "name": "列表测试操作",
            "description": "用于列表测试",
            "steps": [{"action_id": "click", "params": {}}],
        }
        return client.post(f"{PREFIX}/custom-actions/create", json=request_data)


class TestGetCompositeAction:

    def test_get_custom_action_after_create(self, client):
        create_resp = self._create_action(client)
        action_id = create_resp["data"]["id"]

        response = client.post(f"{PREFIX}/custom-actions/get", json={"id": action_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "详情测试操作"
        assert data["data"]["input_vars"] == []
        assert data["data"]["output_vars"] == []

    def test_get_custom_action_not_found(self, client):
        response = client.post(f"{PREFIX}/custom-actions/get", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_action(client):
        request_data = {
            "name": "详情测试操作",
            "description": "用于详情测试",
            "steps": [{"action_id": "click", "params": {}}],
        }
        resp = client.post(f"{PREFIX}/custom-actions/create", json=request_data)
        return resp.json()


class TestUpdateCompositeAction:

    def test_update_custom_action_success(self, client):
        create_resp = self._create_action(client)
        action_id = create_resp["data"]["id"]

        request_data = {
            "id": action_id,
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

        response = client.post(f"{PREFIX}/custom-actions/update", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "更新后的操作"
        assert data["data"]["timeout"] == 60000
        assert data["data"]["retry_on_error"] is True
        assert data["data"]["retry_times"] == 3
        assert data["data"]["retry_delay"] == 2.0

    def test_update_custom_action_not_found(self, client):
        response = client.post(f"{PREFIX}/custom-actions/update", json={"id": 999999, "name": "不存在的操作"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_action(client):
        request_data = {
            "name": "更新测试操作",
            "steps": [{"action_id": "click", "params": {}}],
        }
        resp = client.post(f"{PREFIX}/custom-actions/create", json=request_data)
        return resp.json()


class TestDeleteCompositeAction:

    def test_delete_custom_action_success(self, client):
        create_resp = self._create_action(client)
        action_id = create_resp["data"]["id"]

        response = client.post(f"{PREFIX}/custom-actions/delete", json={"id": action_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    def test_delete_custom_action_not_found(self, client):
        response = client.post(f"{PREFIX}/custom-actions/delete", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_action(client):
        request_data = {
            "name": "删除测试操作",
            "steps": [{"action_id": "click", "params": {}}],
        }
        resp = client.post(f"{PREFIX}/custom-actions/create", json=request_data)
        return resp.json()


class TestForkCompositeAction:

    def test_fork_custom_action_success(self, client):
        self._create_public_action(client)

        list_resp = client.post(f"{PREFIX}/custom-actions/list", json={"page": 1, "per_page": 100})
        action_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["name"] == "Fork源操作":
                action_id = item["id"]
                break

        assert action_id is not None

        response = client.post(f"{PREFIX}/custom_actions/fork", json={"id": action_id, "new_name": "我的 Fork"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert "Fork" in data["data"]["name"]

    def test_fork_custom_action_not_found(self, client):
        response = client.post(f"{PREFIX}/custom_actions/fork", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_public_action(client):
        request_data = {
            "name": "Fork源操作",
            "steps": [{"action_id": "click", "params": {}}],
            "is_public": True,
        }
        return client.post(f"{PREFIX}/custom-actions/create", json=request_data)
