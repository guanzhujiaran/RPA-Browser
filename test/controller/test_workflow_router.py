"""
测试 Workflow 管理接口 - 使用真实数据库操作
使用 httpx.AsyncClient 进行异步测试
参考: https://fastapi.org.cn/advanced/async-tests/
"""
import pytest

from bili_common.models.response_code import ResponseCode

# 复用 conftest 中的 PREFIX
from test.controller.conftest import PREFIX


@pytest.mark.anyio
class TestCreateWorkflow:

    async def test_create_workflow_success(self, client):
        request_data = {
            "name": "测试工作流",
            "description": "这是一个测试工作流",
            "trigger_type": "manual",
            "trigger_config": {},
            "is_public": False,
        }

        response = await client.post(f"{PREFIX}/workflows/create", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "测试工作流"
        assert data["data"]["workflow_id"].startswith("wf_")

    async def test_create_workflow_minimal(self, client):
        request_data = {"name": "最小化工作流"}

        response = await client.post(f"{PREFIX}/workflows/create", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS


@pytest.mark.anyio
class TestListWorkflows:

    async def test_list_workflows_success(self, client, created_workflow):
        response = await client.post(f"{PREFIX}/workflows/list", json={"page": 1, "per_page": 10})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["total"] >= 1
        assert len(data["data"]["items"]) >= 1

    async def test_list_workflows_default_request(self, client):
        response = await client.post(f"{PREFIX}/workflows/list", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS


@pytest.mark.anyio
class TestGetWorkflowDetail:

    async def test_get_workflow_detail_success(self, client, created_workflow):
        db_id, workflow_id = created_workflow

        response = await client.post(f"{PREFIX}/workflows/get", json={"id": db_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "fixture_workflow"

    async def test_get_workflow_detail_missing_id(self, client):
        response = await client.post(f"{PREFIX}/workflows/get", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.BAD_REQUEST, f"Expected BAD_REQUEST, got {data['code']}: {data.get('message')}"

    async def test_get_workflow_detail_not_found(self, client):
        response = await client.post(f"{PREFIX}/workflows/get", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"

    async def test_get_workflow_detail_no_permission(self, client):
        """测试获取不存在的工作流（返回 NOT_FOUND 而非 FORBIDDEN）"""
        response = await client.post(f"{PREFIX}/workflows/get", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"


@pytest.mark.anyio
class TestUpdateWorkflow:

    async def test_update_workflow_success(self, client, created_workflow):
        db_id, workflow_id = created_workflow

        request_data = {
            "id": db_id,
            "name": "更新后的工作流",
            "description": "更新后的描述",
        }

        response = await client.post(f"{PREFIX}/workflows/update", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "更新后的工作流"

    async def test_update_workflow_not_found(self, client):
        response = await client.post(f"{PREFIX}/workflows/update", json={"id": 999, "name": "不存在的"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"


@pytest.mark.anyio
class TestDeleteWorkflow:

    async def test_delete_workflow_success(self, client, created_workflow):
        db_id, workflow_id = created_workflow

        response = await client.post(f"{PREFIX}/workflows/delete", json={"id": db_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_delete_workflow_missing_id(self, client):
        response = await client.post(f"{PREFIX}/workflows/delete", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.BAD_REQUEST, f"Expected BAD_REQUEST, got {data['code']}: {data.get('message')}"

    async def test_delete_workflow_not_found(self, client):
        response = await client.post(f"{PREFIX}/workflows/delete", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"

    async def test_delete_workflow_no_permission(self, client):
        """测试删除不存在的工作流（返回 NOT_FOUND）"""
        response = await client.post(f"{PREFIX}/workflows/delete", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"


@pytest.mark.anyio
class TestDuplicateWorkflow:

    async def test_duplicate_workflow_success(self, client, created_workflow):
        db_id, workflow_id = created_workflow

        response = await client.post(f"{PREFIX}/workflows/duplicate", json={"id": db_id, "new_name": "副本工作流"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_duplicate_workflow_missing_id(self, client):
        response = await client.post(f"{PREFIX}/workflows/duplicate", json={"new_name": "副本"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.BAD_REQUEST, f"Expected BAD_REQUEST, got {data['code']}: {data.get('message')}"

    async def test_duplicate_workflow_missing_new_name(self, client):
        response = await client.post(f"{PREFIX}/workflows/duplicate", json={"id": 1})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.BAD_REQUEST, f"Expected BAD_REQUEST, got {data['code']}: {data.get('message')}"

    async def test_duplicate_workflow_not_found(self, client):
        response = await client.post(f"{PREFIX}/workflows/duplicate", json={"id": 999, "new_name": "副本"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"


@pytest.mark.anyio
class TestForkWorkflow:

    async def test_fork_workflow_success(self, client, created_workflow):
        db_id, workflow_id = created_workflow

        # 先将工作流设为公开
        await client.post(f"{PREFIX}/workflows/update", json={"id": db_id, "is_public": True})

        list_resp = await client.post(f"{PREFIX}/workflows/list", json={"page": 1, "per_page": 100})
        found_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["workflow_id"] == workflow_id:
                found_id = item["id"]
                break

        assert found_id is not None

        response = await client.post(f"{PREFIX}/workflows/fork", json={"id": found_id, "new_name": "我的 Fork"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert "Fork" in data["data"]["name"]

    async def test_fork_workflow_not_found(self, client):
        response = await client.post(f"{PREFIX}/workflows/fork", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"

    async def test_fork_workflow_not_public(self, client, created_workflow):
        db_id, workflow_id = created_workflow
        # created_workflow 默认 is_public=False

        list_resp = await client.post(f"{PREFIX}/workflows/list", json={"page": 1, "per_page": 100})
        found_id = None
        for item in list_resp.json()["data"]["items"]:
            if item["workflow_id"] == workflow_id:
                found_id = item["id"]
                break

        assert found_id is not None

        response = await client.post(f"{PREFIX}/workflows/fork", json={"id": found_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.FORBIDDEN, f"Expected FORBIDDEN, got {data['code']}: {data.get('message')}"


@pytest.mark.anyio
class TestGetWorkflowForks:

    async def test_get_workflow_forks_success(self, client, created_workflow):
        db_id, workflow_id = created_workflow

        response = await client.get(f"{PREFIX}/workflows/{db_id}/forks")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_get_workflow_forks_not_found(self, client):
        response = await client.get(f"{PREFIX}/workflows/999999/forks")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.NOT_FOUND, f"Expected NOT_FOUND, got {data['code']}: {data.get('message')}"


@pytest.mark.anyio
class TestExecuteWorkflow:

    async def test_execute_workflow_missing_action_id(self, client):
        """测试：缺少 action_id 时返回 BAD_REQUEST"""
        request_data = {
            "browser_id": 1,
            "variables": {"key": "value"},
            "input_data": {},
            "output": [],
        }

        response = await client.post(f"{PREFIX}/workflows/execute", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.BAD_REQUEST, f"Expected BAD_REQUEST, got {data['code']}: {data.get('message')}"


@pytest.mark.anyio
class TestExecuteWorkflowInlineSteps:
    """测试内联步骤(inline steps)执行路径的步骤规范化逻辑"""

    async def test_create_workflow_step_normalization_basic(self):
        """测试：create_workflow_step 能正确将 BaseWorkflowStep 规范化为类型化步骤"""
        from app.models.execution.action_params import create_workflow_step, BaseWorkflowStep

        # 模拟 _build_steps 的输出
        built_steps = [
            BaseWorkflowStep(action_id="click", params={"selector": "#btn"}, retry=1),
            BaseWorkflowStep(action_id="input", params={"selector": "#x", "value": "hi"}, loop_count=3),
            BaseWorkflowStep(action_id="navigate", params={"url": "https://example.com"}),
            BaseWorkflowStep(action_id="screenshot", params={}, output_var="img"),
        ]

        normalized = [
            create_workflow_step(
                action_id=s.action_id,
                params=s.params or {},
                retry=s.retry or 0,
                loop_count=s.loop_count,
                loop_while=s.loop_while,
                loop_until=s.loop_until,
                condition=s.condition,
                output_var=s.output_var,
            )
            for s in built_steps
        ]

        assert len(normalized) == 4
        # 验证子类类型正确
        from app.models.execution.action_params import (
            ClickWorkflowStep, InputWorkflowStep,
            NavigateWorkflowStep, ScreenshotWorkflowStep,
        )
        assert isinstance(normalized[0], ClickWorkflowStep)
        assert isinstance(normalized[1], InputWorkflowStep)
        assert isinstance(normalized[2], NavigateWorkflowStep)
        assert isinstance(normalized[3], ScreenshotWorkflowStep)

        # 验证字段保留
        assert normalized[0].retry == 1
        assert normalized[1].loop_count == 3
        assert normalized[3].output_var == "img"

    async def test_create_workflow_step_normalization_with_children(self):
        """测试：create_workflow_step 正确处理嵌套子步骤（循环/条件分支）"""
        from app.models.execution.action_params import create_workflow_step, BaseWorkflowStep
        from app.models.execution.condition_models import (
            ConditionRule, ParamsCondition, ConditionValueType, LogicOperator,
        )

        # 构建子步骤
        children = [
            BaseWorkflowStep(action_id="click", params={"selector": ".item"}),
            BaseWorkflowStep(action_id="screenshot", params={}),
        ]

        # 构建 if_else 步骤（含 children）
        condition_rule = ConditionRule(
            logic=LogicOperator.AND,
            condition=ParamsCondition(
                field="is_active",
                condition_value_type=ConditionValueType.BOOLEAN,
                condition_value=True,
            ),
        )

        built_steps = [
            BaseWorkflowStep(
                action_id="if_else",
                params={"condition": condition_rule.model_dump()},
                children=children,
            ),
        ]

        normalized = [
            create_workflow_step(
                action_id=s.action_id,
                params=s.params or {},
                retry=s.retry or 0,
                loop_count=s.loop_count,
                loop_while=s.loop_while,
                loop_until=s.loop_until,
                condition=s.condition,
                children=(
                    [create_workflow_step(
                        action_id=c.action_id,
                        params=c.params or {},
                        retry=c.retry or 0,
                    ) for c in s.children]
                    if s.children else None
                ),
                output_var=s.output_var,
            )
            for s in built_steps
        ]

        assert len(normalized) == 1
        from app.models.execution.action_params import IfElseWorkflowStep
        assert isinstance(normalized[0], IfElseWorkflowStep)
        assert normalized[0].children is not None
        assert len(normalized[0].children) == 2

    async def test_create_workflow_step_custom_action(self):
        """测试：create_workflow_step 对自定义 action_id 返回 BaseWorkflowStep"""
        from app.models.execution.action_params import create_workflow_step, BaseWorkflowStep

        step = create_workflow_step(
            action_id="ca_custom_action_123",
            params={"key": "value"},
            retry=2,
        )

        assert isinstance(step, BaseWorkflowStep)
        assert step.action_id == "ca_custom_action_123"
        assert step.retry == 2

    async def test_create_workflow_step_invalid_params_degradation(self):
        """测试：参数校验失败时不崩溃，降级为 BaseWorkflowStep + 原始 dict"""
        from app.models.execution.action_params import create_workflow_step, BaseWorkflowStep

        # LLM 类型需要 messages 为 list，但传入非法值
        step = create_workflow_step(
            action_id="llm",
            params={"messages": "", "model": "gpt-4"},
            retry=1,
        )

        # 应降级为 BaseWorkflowStep，而非崩溃
        assert isinstance(step, BaseWorkflowStep)
        assert step.action_id == "llm"
        assert step.params == {"messages": "", "model": "gpt-4"}
        assert step.retry == 1
