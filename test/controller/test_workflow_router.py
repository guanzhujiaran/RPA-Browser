"""
测试 Workflow 管理接口 - 使用真实数据库操作
使用 httpx.AsyncClient 进行异步测试
参考: https://fastapi.org.cn/advanced/async-tests/

契约要点（与 app/controller/.../workflow_router.py 对齐）：
- workflow_id 是雪花 int（不再有 "wf_" 前缀），CRUD 用数据库主键 ``id`` 定位；
- 「工作流不存在 / 无权访问」统一走业务码 ``WORKFLOW_NOT_FOUND``（HTTP 200），
  不再返回 404 / 403，避免泄露资源存在性；
- 发布公开（is_public=true）需要管理员审批，这类用例不在离线测试里覆盖。
"""

import pytest

from bili_common.models.response_code import ResponseCode

# 复用 conftest 中的 PREFIX
from test.controller.conftest import OTHER_MID, PREFIX, TEST_MID


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
        # 雪花 int：> 0 且是整数，不再是 "wf_xxx" 字符串
        assert isinstance(data["data"]["workflow_id"], int)
        assert data["data"]["workflow_id"] > 0
        assert isinstance(data["data"]["id"], int)

    async def test_create_workflow_minimal(self, client):
        request_data = {"name": "最小化工作流"}

        response = await client.post(f"{PREFIX}/workflows/create", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS


@pytest.mark.anyio
class TestListWorkflows:
    async def test_list_workflows_success(self, client, created_workflow):
        response = await client.post(
            f"{PREFIX}/workflows/list", json={"page": 1, "per_page": 10}
        )

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
class TestAnonymousCommunityWorkflows:
    """未登录用户（社区广场游客）只能看到公开工作流"""

    @staticmethod
    async def _create_public_workflow(name: str):
        from app.services.execution.crud_service import workflow_crud_svr

        return await workflow_crud_svr.create(
            mid=OTHER_MID,
            name=name,
            custom_action_id=None,
            is_public=True,
        )

    async def test_community_list_returns_public_workflows(self, anon_client):
        public = await self._create_public_workflow("他人的公开工作流")

        response = await anon_client.post(
            f"{PREFIX}/workflows/list",
            json={"page": 1, "per_page": 10, "filter_type": "community"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        ids = [item["id"] for item in data["data"]["items"]]
        assert public.id in ids

    async def test_private_list_is_empty_for_anonymous(self, anon_client, created_workflow):
        response = await anon_client.post(
            f"{PREFIX}/workflows/list",
            json={"page": 1, "per_page": 10, "filter_type": "private"},
        )

        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["total"] == 0
        assert data["data"]["items"] == []


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
        # 请求体是 WorkflowIdRequest(id 必填)：缺参在进入处理函数前就被 pydantic 拒绝
        response = await client.post(f"{PREFIX}/workflows/get", json={})

        assert response.status_code == 422
        assert "id" in str(response.json())

    async def test_get_workflow_detail_not_found(self, client):
        response = await client.post(f"{PREFIX}/workflows/get", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.WORKFLOW_NOT_FOUND, (
            f"Expected WORKFLOW_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )

    async def test_get_workflow_detail_no_permission(self, client):
        """测试获取不存在的工作流（返回 NOT_FOUND 而非 FORBIDDEN）"""
        response = await client.post(f"{PREFIX}/workflows/get", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.WORKFLOW_NOT_FOUND, (
            f"Expected WORKFLOW_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )


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
        response = await client.post(
            f"{PREFIX}/workflows/update", json={"id": 999, "name": "不存在的"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.WORKFLOW_NOT_FOUND, (
            f"Expected WORKFLOW_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )


@pytest.mark.anyio
class TestDeleteWorkflow:
    async def test_delete_workflow_success(self, client, created_workflow):
        db_id, workflow_id = created_workflow

        response = await client.post(f"{PREFIX}/workflows/delete", json={"id": db_id})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_delete_workflow_missing_id(self, client):
        # 请求体是 WorkflowIdRequest(id 必填)：缺参在进入处理函数前就被 pydantic 拒绝
        response = await client.post(f"{PREFIX}/workflows/delete", json={})

        assert response.status_code == 422
        assert "id" in str(response.json())

    async def test_delete_workflow_not_found(self, client):
        response = await client.post(f"{PREFIX}/workflows/delete", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.WORKFLOW_NOT_FOUND, (
            f"Expected WORKFLOW_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )

    async def test_delete_workflow_no_permission(self, client):
        """测试删除不存在的工作流（返回 NOT_FOUND）"""
        response = await client.post(f"{PREFIX}/workflows/delete", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.WORKFLOW_NOT_FOUND, (
            f"Expected WORKFLOW_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )


@pytest.mark.anyio
class TestDuplicateWorkflow:
    async def test_duplicate_workflow_success(self, client, created_workflow):
        db_id, workflow_id = created_workflow

        response = await client.post(
            f"{PREFIX}/workflows/duplicate",
            json={"id": db_id, "new_name": "副本工作流"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS

    async def test_duplicate_workflow_missing_id(self, client):
        # WorkflowDuplicateRequest：id / new_name 都必填，缺参在进入处理函数前被拒
        response = await client.post(
            f"{PREFIX}/workflows/duplicate", json={"new_name": "副本"}
        )

        assert response.status_code == 422
        assert "id" in str(response.json())

    async def test_duplicate_workflow_missing_new_name(self, client):
        response = await client.post(f"{PREFIX}/workflows/duplicate", json={"id": 1})

        assert response.status_code == 422
        assert "new_name" in str(response.json())

    async def test_duplicate_workflow_not_found(self, client):
        response = await client.post(
            f"{PREFIX}/workflows/duplicate", json={"id": 999, "new_name": "副本"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.WORKFLOW_NOT_FOUND, (
            f"Expected WORKFLOW_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )


@pytest.mark.anyio
class TestForkWorkflow:
    async def test_fork_workflow_success(self, client, created_workflow):
        db_id, workflow_id = created_workflow

        # 自己的资源允许直接 Fork，无需先发布（发布公开要管理员审批）
        response = await client.post(
            f"{PREFIX}/workflows/fork", json={"id": db_id, "new_name": "我的 Fork"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert data["data"]["name"] == "我的 Fork"
        assert data["data"]["forked_from"] == workflow_id

    async def test_fork_workflow_not_found(self, client):
        response = await client.post(f"{PREFIX}/workflows/fork", json={"id": 999999})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.WORKFLOW_NOT_FOUND, (
            f"Expected WORKFLOW_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )

    async def test_fork_workflow_not_public(self, client):
        """他人的非公开工作流不允许 Fork（只能 Fork 公开的或自己的）"""
        from app.services.execution.crud_service import workflow_crud_svr

        other = await workflow_crud_svr.create(
            mid=OTHER_MID,
            name="他人私有工作流",
            custom_action_id=None,
            is_public=False,
        )

        response = await client.post(
            f"{PREFIX}/workflows/fork", json={"id": other.id}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.FORBIDDEN, (
            f"Expected FORBIDDEN, got {data['code']}: {data.get('msg')}"
        )

    async def test_fork_workflow_copies_all_create_fields(self, client):
        """Fork 复用「导出 → 导入」字段口径：外壳参数不得漏拷。"""
        create_resp = await client.post(
            f"{PREFIX}/workflows/create",
            json={
                "name": "完整参数工作流",
                "description": "完整参数描述",
                "browser_id": 123456,
                "browser_ids": [123456, 123457],
                "concurrent_exec": True,
                "trigger_type": "manual",
                "trigger_config": {},
                "is_public": False,
                "notify_inbox_on_start": True,
                "notify_inbox_on_end": False,
                "notify_push_on_start": True,
                "notify_push_on_end": False,
                "log_enabled": True,
                "log_debug": True,
                "resume_on_restart": True,
                "variables": {"token": "abc"},
            },
        )
        origin = create_resp.json()["data"]

        fork_resp = await client.post(
            f"{PREFIX}/workflows/fork",
            json={"id": origin["id"], "new_name": "完整参数工作流 Fork"},
        )
        fork_data = fork_resp.json()
        assert fork_data["code"] == ResponseCode.SUCCESS, fork_data.get("msg")

        detail_resp = await client.post(
            f"{PREFIX}/workflows/get", json={"id": fork_data["data"]["id"]}
        )
        detail = detail_resp.json()["data"]
        assert detail["browser_id"] == 123456
        assert detail["browser_ids"] == [123456, 123457]
        assert detail["concurrent_exec"] is True
        assert detail["description"] == "完整参数描述"
        assert detail["notify_inbox_on_start"] is True
        assert detail["notify_inbox_on_end"] is False
        assert detail["notify_push_on_start"] is True
        assert detail["notify_push_on_end"] is False
        assert detail["log_enabled"] is True
        assert detail["log_debug"] is True
        assert detail["resume_on_restart"] is True
        assert detail["variables"] == {"token": "abc"}
        assert detail["is_public"] is False
        assert detail["forked_from_id"] == origin["id"]

        # 上游副本计数 +1
        origin_detail = (
            await client.post(f"{PREFIX}/workflows/get", json={"id": origin["id"]})
        ).json()["data"]
        assert origin_detail["forks_count"] == 1

    async def test_fork_public_workflow_copies_action(self, client):
        """Fork 他人公开工作流：动作依赖树一起 Fork，副本自包含且参数不丢"""
        from app.models.database.workflow.models import BuiltinActionType
        from app.services.execution.crud_service import (
            action_crud_svr,
            workflow_crud_svr,
        )

        action = await action_crud_svr.create(
            mid=OTHER_MID,
            name="他人公开动作",
            action_type=BuiltinActionType.COMPOSITE,
            steps=[{"action_id": "click", "params": {}}],
            is_composite=True,
            is_public=True,
            timeout=60000,
            retry_on_error=True,
            retry_times=3,
            retry_delay=2.5,
        )
        other = await workflow_crud_svr.create(
            mid=OTHER_MID,
            name="他人公开工作流",
            custom_action_id=action.action_id,
            is_public=True,
        )

        response = await client.post(f"{PREFIX}/workflows/fork", json={"id": other.id})
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS, data
        assert "已独立复制 1 个关联动作" in data["msg"]

        fork_detail = (
            await client.post(
                f"{PREFIX}/workflows/get", json={"id": data["data"]["id"]}
            )
        ).json()["data"]
        assert fork_detail["custom_action_id"] != action.action_id

        # 副本动作归属当前用户，且完整复制执行参数（不再丢 retry / timeout）
        forked_action = await action_crud_svr.get_by_action_id(
            fork_detail["custom_action_id"]
        )
        assert forked_action is not None
        assert str(forked_action.mid) == str(TEST_MID)
        assert forked_action.timeout == 60000
        assert forked_action.retry_on_error is True
        assert forked_action.retry_times == 3
        assert forked_action.retry_delay == 2.5
        assert forked_action.is_enabled is True

    async def test_fork_public_workflow_rejects_private_action(self, client):
        """Fork 他人公开工作流：引用的私有动作不可见，按后端 msg 拒绝"""
        from app.models.database.workflow.models import BuiltinActionType
        from app.services.execution.crud_service import (
            action_crud_svr,
            workflow_crud_svr,
        )

        action = await action_crud_svr.create(
            mid=OTHER_MID,
            name="他人私有动作",
            action_type=BuiltinActionType.COMPOSITE,
            steps=[{"action_id": "click", "params": {}}],
            is_composite=True,
            is_public=False,
        )
        other = await workflow_crud_svr.create(
            mid=OTHER_MID,
            name="引用私有动作的公开工作流",
            custom_action_id=action.action_id,
            is_public=True,
        )

        response = await client.post(f"{PREFIX}/workflows/fork", json={"id": other.id})
        data = response.json()
        assert data["code"] == ResponseCode.FORBIDDEN, data
        assert "无权访问操作" in data["msg"]


@pytest.mark.anyio
class TestImportWorkflowValidation:
    """导入分享 JSON：动作引用要校验，浏览器 id 只在运行时校验"""

    @staticmethod
    async def _create_action(mid: int, name: str, is_public: bool) -> int:
        from app.models.database.workflow.models import BuiltinActionType
        from app.services.execution.crud_service import action_crud_svr

        model = await action_crud_svr.create(
            mid=mid,
            name=name,
            action_type=BuiltinActionType.COMPOSITE,
            steps=[{"action_id": "click", "params": {}}],
            is_composite=True,
            is_public=is_public,
        )
        return model.action_id

    async def test_import_rejects_private_action_of_other_user(self, client):
        action_id = await self._create_action(OTHER_MID, "他人私有动作", False)

        response = await client.post(
            f"{PREFIX}/workflows/import",
            json={"name": "导入他人私有动作", "custom_action_id": action_id},
        )
        data = response.json()
        assert data["code"] == ResponseCode.BUSINESS_ERROR, (
            f"Expected BUSINESS_ERROR, got {data['code']}: {data.get('msg')}"
        )
        assert "无权访问" in data["msg"]

    async def test_import_rejects_unknown_action(self, client):
        response = await client.post(
            f"{PREFIX}/workflows/import",
            json={"name": "导入不存在动作", "custom_action_id": 999999},
        )
        data = response.json()
        assert data["code"] == ResponseCode.BUSINESS_ERROR
        assert "不存在" in data["msg"]

    async def test_import_accepts_public_action(self, client):
        action_id = await self._create_action(OTHER_MID, "他人公开动作", True)

        response = await client.post(
            f"{PREFIX}/workflows/import",
            json={"name": "导入公开动作", "custom_action_id": action_id},
        )
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS, data.get("msg")

        detail = (
            await client.post(
                f"{PREFIX}/workflows/get", json={"id": data["data"]["id"]}
            )
        ).json()["data"]
        assert detail["custom_action_id"] == action_id

    async def test_import_skips_browser_ownership_validation(self, client):
        """分享 JSON 里的浏览器可能不属于当前用户：导入放行，运行时再校验"""
        response = await client.post(
            f"{PREFIX}/workflows/import",
            json={
                "name": "导入他人浏览器工作流",
                "browser_id": 987654321,
                "browser_ids": [987654321, 987654322],
            },
        )
        data = response.json()
        assert data["code"] == ResponseCode.SUCCESS, data.get("msg")

        detail = (
            await client.post(
                f"{PREFIX}/workflows/get", json={"id": data["data"]["id"]}
            )
        ).json()["data"]
        assert detail["browser_id"] == 987654321
        assert detail["browser_ids"] == [987654321, 987654322]


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
        assert data["code"] == ResponseCode.WORKFLOW_NOT_FOUND, (
            f"Expected WORKFLOW_NOT_FOUND, got {data['code']}: {data.get('msg')}"
        )


@pytest.mark.anyio
class TestExecuteWorkflow:
    async def test_execute_workflow_missing_action_id(self, client, monkeypatch):
        """既没有 action_id 也没有 steps 时返回 BUSINESS_ERROR

        现在的 ``/workflows/execute`` 从浏览器归属依赖里取 mid/browser_id，
        并要解析一个真实页面；离线测试只需要把页面解析换掉，
        验证「两个入口都没给」时的兜底提示。
        """
        from app.controller.v1.browser_control.execution import execution_router

        async def _fake_resolve_page(mid, browser_id, page_index=None):
            return object()

        monkeypatch.setattr(execution_router, "_resolve_page", _fake_resolve_page)

        response = await client.post(
            f"{PREFIX}/workflows/execute", json={"variables": {"key": "value"}}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == ResponseCode.BUSINESS_ERROR, (
            f"Expected BUSINESS_ERROR, got {data['code']}: {data.get('msg')}"
        )
        assert "action_id" in data["msg"]


@pytest.mark.anyio
class TestExecuteWorkflowInlineSteps:
    """测试内联步骤(inline steps)执行路径的步骤规范化逻辑"""

    async def test_create_workflow_step_normalization_basic(self):
        """测试：create_workflow_step 能正确将 BaseWorkflowStep 规范化为类型化步骤"""
        from app.models.execution.action_params import (
            create_workflow_step,
            BaseWorkflowStep,
        )

        # 模拟 _build_steps 的输出
        built_steps = [
            BaseWorkflowStep(action_id="click", params={"selector": "#btn"}, retry=1),
            BaseWorkflowStep(
                action_id="input",
                params={"selector": "#x", "value": "hi"},
                loop_count=3,
            ),
            BaseWorkflowStep(
                action_id="navigate", params={"url": "https://example.com"}
            ),
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
            ClickWorkflowStep,
            InputWorkflowStep,
            NavigateWorkflowStep,
            ScreenshotWorkflowStep,
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
        from app.models.execution.action_params import (
            create_workflow_step,
            BaseWorkflowStep,
        )
        from app.models.execution.condition_models import (
            ConditionRule,
            ParamsCondition,
            ConditionValueType,
            LogicOperator,
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
                    [
                        create_workflow_step(
                            action_id=c.action_id,
                            params=c.params or {},
                            retry=c.retry or 0,
                        )
                        for c in s.children
                    ]
                    if s.children
                    else None
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
        from app.models.execution.action_params import (
            create_workflow_step,
            BaseWorkflowStep,
        )

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
        from app.models.execution.action_params import (
            create_workflow_step,
            BaseWorkflowStep,
        )

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
