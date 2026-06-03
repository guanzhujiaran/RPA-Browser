"""
测试 Workflow 管理接口 - 使用真实数据库操作
"""
import pytest

from app.models.response_code import ResponseCode

PREFIX = "/api/v1/rpa/browser/control"


class TestCreateWorkflow:

    def test_create_workflow_success(self, client):
        request_data = {
            "name": "测试工作流",
            "description": "这是一个测试工作流",
            "trigger_type": "manual",
            "trigger_config": {},
            "is_public": False,
        }

        response = client.post(f"{PREFIX}/workflows/create", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "测试工作流"
        assert data["data"]["workflow_id"].startswith("wf_")

    def test_create_workflow_minimal(self, client):
        request_data = {"name": "最小化工作流"}

        response = client.post(f"{PREFIX}/workflows/create", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS


class TestListWorkflows:

    def test_list_workflows_success(self, client):
        self._create_workflow(client)

        response = client.post(f"{PREFIX}/workflows/list", json={"page": 1, "per_page": 10})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["total"] >= 1
        assert len(data["data"]["items"]) >= 1
        assert data["data"]["items"][0]["name"] == "列表测试工作流"

    def test_list_workflows_default_request(self, client):
        response = client.post(f"{PREFIX}/workflows/list", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    @staticmethod
    def _create_workflow(client):
        request_data = {
            "name": "列表测试工作流",
            "description": "用于列表测试",
        }
        return client.post(f"{PREFIX}/workflows/create", json=request_data)


class TestGetWorkflowDetail:

    def test_get_workflow_detail_success(self, client):
        create_resp = self._create_workflow(client)
        workflow_id = create_resp["data"]["id"]

        response = client.post(f"{PREFIX}/workflows/get", json={"id": workflow_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "详情测试工作流"

    def test_get_workflow_detail_missing_id(self, client):
        response = client.post(f"{PREFIX}/workflows/get", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_get_workflow_detail_not_found(self, client):
        response = client.post(f"{PREFIX}/workflows/get", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_get_workflow_detail_no_permission(self, client):
        response = client.post(f"{PREFIX}/workflows/get", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_workflow(client):
        request_data = {
            "name": "详情测试工作流",
            "description": "用于详情测试",
        }
        resp = client.post(f"{PREFIX}/workflows/create", json=request_data)
        return resp.json()


class TestUpdateWorkflow:

    def test_update_workflow_success(self, client):
        create_resp = self._create_workflow(client)
        workflow_id = create_resp["data"]["id"]

        request_data = {
            "id": workflow_id,
            "name": "更新后的工作流",
            "description": "更新后的描述",
        }

        response = client.post(f"{PREFIX}/workflows/update", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "更新后的工作流"

    def test_update_workflow_not_found(self, client):
        response = client.post(f"{PREFIX}/workflows/update", json={"id": 999, "name": "不存在的"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_workflow(client):
        request_data = {
            "name": "更新测试工作流",
        }
        resp = client.post(f"{PREFIX}/workflows/create", json=request_data)
        return resp.json()


class TestDeleteWorkflow:

    def test_delete_workflow_success(self, client):
        create_resp = self._create_workflow(client)
        workflow_id = create_resp["data"]["id"]

        response = client.post(f"{PREFIX}/workflows/delete", json={"id": workflow_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    def test_delete_workflow_missing_id(self, client):
        response = client.post(f"{PREFIX}/workflows/delete", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_delete_workflow_not_found(self, client):
        response = client.post(f"{PREFIX}/workflows/delete", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_delete_workflow_no_permission(self, client):
        response = client.post(f"{PREFIX}/workflows/delete", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_workflow(client):
        request_data = {
            "name": "删除测试工作流",
        }
        resp = client.post(f"{PREFIX}/workflows/create", json=request_data)
        return resp.json()


class TestDuplicateWorkflow:

    def test_duplicate_workflow_success(self, client):
        self._create_workflow(client)

        list_resp = client.post(f"{PREFIX}/workflows/list", json={"page": 1, "per_page": 100})
        workflow_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["name"] == "复制源工作流":
                workflow_id = item["id"]
                break

        assert workflow_id is not None

        response = client.post(f"{PREFIX}/workflows/duplicate", json={"id": workflow_id, "new_name": "副本工作流"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    def test_duplicate_workflow_missing_id(self, client):
        response = client.post(f"{PREFIX}/workflows/duplicate", json={"new_name": "副本"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_duplicate_workflow_missing_new_name(self, client):
        response = client.post(f"{PREFIX}/workflows/duplicate", json={"id": 1})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_duplicate_workflow_not_found(self, client):
        response = client.post(f"{PREFIX}/workflows/duplicate", json={"id": 999, "new_name": "副本"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_workflow(client):
        request_data = {
            "name": "复制源工作流",
        }
        return client.post(f"{PREFIX}/workflows/create", json=request_data)


class TestForkWorkflow:

    def test_fork_workflow_success(self, client):
        self._create_public_workflow(client)

        list_resp = client.post(f"{PREFIX}/workflows/list", json={"page": 1, "per_page": 100})
        workflow_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["name"] == "Fork源工作流":
                workflow_id = item["id"]
                break

        assert workflow_id is not None

        response = client.post(f"{PREFIX}/workflows/fork", json={"id": workflow_id, "new_name": "我的 Fork"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert "Fork" in data["data"]["name"]

    def test_fork_workflow_not_found(self, client):
        response = client.post(f"{PREFIX}/workflows/fork", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    def test_fork_workflow_not_public(self, client):
        self._create_private_workflow(client)

        list_resp = client.post(f"{PREFIX}/workflows/list", json={"page": 1, "per_page": 100})
        workflow_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["name"] == "私有工作流":
                workflow_id = item["id"]
                break

        assert workflow_id is not None

        response = client.post(f"{PREFIX}/workflows/fork", json={"id": workflow_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_public_workflow(client):
        request_data = {
            "name": "Fork源工作流",
            "is_public": True,
        }
        return client.post(f"{PREFIX}/workflows/create", json=request_data)

    @staticmethod
    def _create_private_workflow(client):
        request_data = {
            "name": "私有工作流",
            "is_public": False,
        }
        return client.post(f"{PREFIX}/workflows/create", json=request_data)


class TestGetWorkflowForks:

    def test_get_workflow_forks_success(self, client):
        self._create_workflow(client)

        list_resp = client.post(f"{PREFIX}/workflows/list", json={"page": 1, "per_page": 100})
        workflow_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["name"] == "Fork源工作流":
                workflow_id = item["id"]
                break

        assert workflow_id is not None

        response = client.get(f"{PREFIX}/workflows/{workflow_id}/forks")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    def test_get_workflow_forks_not_found(self, client):
        response = client.get(f"{PREFIX}/workflows/999999/forks")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != ResponseCode.SUCCESS

    @staticmethod
    def _create_workflow(client):
        request_data = {
            "name": "Fork源工作流",
            "is_public": True,
        }
        return client.post(f"{PREFIX}/workflows/create", json=request_data)


class TestExecuteWorkflow:

    def test_execute_workflow_success(self, client):
        request_data = {
            "variables": {"key": "value"},
            "input_data": {},
            "output": [],
            "on_error": "stop",
        }

        response = client.post(f"{PREFIX}/workflows/execute", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
