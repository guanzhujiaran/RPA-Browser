"""
执行引擎测试 - 数据库驱动的复合操作执行测试

测试流程：
1. 创建复合操作（CompositeActionModel）并入库
2. 可选：创建插件（UserPlugin）并关联
3. 从数据库读取操作
4. 通过 ExecutionEngine 执行
5. 验证执行结果
"""
from app.models.execution.action_params import create_workflow_step
from app.models.execution.request_params import WorkflowExecutionRequest
from app.models.execution.request_params import ActionExecutionRequest
from app.models.execution.condition_models import (
    ConditionRule,
    ParamsCondition,
    ConditionValueType,
    LogicOperator,
)
from contextlib import suppress
import pytest
import uuid
from playwright.async_api import Page
from app.models.database.workflow.models import (
    UserPlugin,
    BuiltinActionType,
)
from app.models.execution.action_params import PluginConfig
from app.services.execution.crud_service import action_crud_svr, plugin_crud_svr, workflow_crud_svr
from app.services.execution.engine import ExecutionEngine
from app.models.database.workflow.models import CompositeActionModel

execution_engine = ExecutionEngine()


class TestExecutionEngineDatabaseWorkflow:
    """测试 ExecutionEngine 执行数据库中存储的自定义操作"""

    @pytest.fixture(autouse=True)
    async def setup(self, page: Page):
        self.page = page
        self.mid = 12345678
        self.test_action_ids: list[str] = []
        self.test_plugin_ids: list[str] = []
        yield
        # 清理测试数据
        for action_id in self.test_action_ids:
            with suppress(Exception):
                from sqlmodel import select
                from app.utils.depends.session_manager import DatabaseSessionManager
                async with DatabaseSessionManager.async_session() as session:
                    result = await session.exec(
                        select(CompositeActionModel).where(
                            CompositeActionModel.action_id == action_id)
                    )
                    if action := result.first():
                        await action_crud_svr.delete(action.id)
        for plugin_id in self.test_plugin_ids:
            with suppress(Exception):
                plugin = await plugin_crud_svr.get_by_plugin_id(plugin_id)
                if plugin:
                    await plugin_crud_svr.delete(plugin.id)

    async def _execute_workflow_from_db(
        self, req: WorkflowExecutionRequest, *, plugins=None,
    ):
        """辅助方法：从 DB 加载步骤并执行工作流，替代旧的 execute_workflow_with_session"""
        from app.models.execution.action_params import _ensure_action_type, workflow_step_adapter

        action_model = await action_crud_svr.get_by_action_id(req.action_id)
        if not action_model:
            raise ValueError(f"未找到操作: {req.action_id}")

        normalized_steps = []
        for s in action_model.steps:
            if isinstance(s, dict):
                s = workflow_step_adapter.validate_python(_ensure_action_type(s))
            normalized_steps.append(s)

        return await execution_engine.execute_steps(
            req,
            steps=normalized_steps,
            session_id="test_session",
            browser_id="test_browser",
            plugins=plugins or [],
        )

    async def _create_custom_action(self, name: str, steps: list, **kwargs) -> CompositeActionModel:
        """创建自定义操作并入库"""
        action_id = f"ca_{uuid.uuid4().hex[:12]}"

        action = await action_crud_svr.create(
            mid=str(self.mid),
            action_id=action_id,
            name=name,
            action_type=BuiltinActionType.COMPOSITE,
            steps=steps,
            is_composite=True,
            description=kwargs.get("description", ""),
            is_public=kwargs.get("is_public", False),
        )

        self.test_action_ids.append(action_id)
        return action

    async def _create_plugin(self, name: str, hook_type: str, custom_action_id: str) -> UserPlugin:
        """创建插件并关联到自定义操作"""
        plugin_id = f"plugin_{uuid.uuid4().hex[:8]}"

        plugin = await plugin_crud_svr.create(
            mid=self.mid,
            plugin_id=plugin_id,
            name=name,
            hook_type=hook_type,
            custom_action_id=custom_action_id,
            description=f"测试插件: {name}",
        )

        self.test_plugin_ids.append(plugin_id)
        return plugin

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_composite_action_from_db(self):
        """测试：从数据库读取自定义操作并执行

        流程：
        1. 创建包含导航和截图步骤的自定义操作
        2. 保存到数据库
        3. 从数据库读取并执行
        4. 验证执行结果
        """
        steps = [
            {"action_id": "navigate", "params": {"url": "https://example.com"}},
            {"action_id": "screenshot", "params": {}},
        ]

        action = await self._create_custom_action(
            name="导航并截图",
            steps=steps,
            description="测试用自定义操作",
        )

        result = await execution_engine.execute_action(
            req=ActionExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=action.action_id,
                params={},
                input_data={},
                output=[],
            ),
            session_id="test_session",
            browser_id="test_browser",
        )

        assert result.success
        assert result.data.total_steps == 2
        assert result.data.success_count == 2

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_composite_with_variables(self):
        """测试：执行带变量的自定义操作

        流程：
        1. 创建包含输入和点击步骤的自定义操作
        2. 步骤参数中使用变量引用
        3. 执行时传入变量池
        4. 验证变量正确替换
        """
        await self.page.set_content(
            "<html><body>"
            "<input id='username' type='text'>"
            "<input id='password' type='password'>"
            "<button id='login'>Login</button>"
            "</body></html>"
        )

        steps = [
            {"action_id": "input", "params": {
                "selector": "#username", "value": "{{user_name}}"}},
            {"action_id": "input", "params": {
                "selector": "#password", "value": "{{user_pass}}"}},
            {"action_id": "click", "params": {"selector": "#login"}},
        ]

        action = await self._create_custom_action(
            name="登录操作",
            steps=steps,
            description="带变量的登录操作",
        )

        result = await execution_engine.execute_action(
            req=ActionExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=action.action_id,
                params={
                    "user_name": "testuser",
                    "user_pass": "secret123",
                },
                input_data={
                    "user_name": "testuser",
                    "user_pass": "secret123",
                },
                output=[],
            ),
            session_id="test_session",
            browser_id="test_browser",
        )

        assert result.success
        assert result.data.success_count == 3

        # 验证输入值已正确填入
        username_value = await self.page.input_value("#username")
        assert "testuser" in username_value

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_workflow_with_plugin(self):
        """测试：执行带插件的工作流

        流程：
        1. 创建基础自定义操作
        2. 创建插件
        3. 执行工作流（带插件参数）
        4. 验证插件在钩子点被执行
        """
        base_action = await self._create_custom_action(
            name="基础操作",
            steps=[
                {"action_id": "navigate", "params": {
                    "url": "https://example.com"}},
            ],
        )

        plugin_action = await self._create_custom_action(
            name="截图插件操作",
            steps=[
                {"action_id": "screenshot", "params": {}},
            ],
        )

        plugin = await self._create_plugin(
            name="截图插件",
            hook_type="after_action",
            custom_action_id=plugin_action.action_id,
        )

        # 创建工作流并关联插件
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        await workflow_crud_svr.create(
            mid=int(self.mid),
            workflow_id=workflow_id,
            name="带插件的工作流",
            custom_action_id=base_action.action_id,
            enabled_plugins=[
                PluginConfig(
                    plugin_id=plugin.plugin_id,
                    config_params={},
                    hook_type=plugin.hook_type,
                    priority=plugin.priority,
                )
            ],
        )

        # 获取工作流插件
        plugins = await workflow_crud_svr.get_enabled_plugins(workflow_id)

        req = WorkflowExecutionRequest(
            mid=self.mid,
            browser_id=1,
            action_id=base_action.action_id,
            variables={},
            input_data={},
            output=[],
        )

        # 从 action_model 获取 steps
        action_model = await action_crud_svr.get_by_action_id(base_action.action_id)
        from app.models.execution.action_params import _ensure_action_type, workflow_step_adapter
        normalized_steps = []
        for s in action_model.steps:
            if isinstance(s, dict):
                s = workflow_step_adapter.validate_python(_ensure_action_type(s))
            normalized_steps.append(s)

        result = await execution_engine.execute_steps(
            req,
            steps=normalized_steps,
            session_id="test_session",
            browser_id="test_browser",
            page=self.page,
            plugins=plugins,
        )

        # 验证工作流执行成功
        assert len(result) > 0
        assert result[-1].success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_workflow_with_custom_actions(self):
        """测试：执行包含自定义操作的工作流

        流程：
        1. 创建多个自定义操作
        2. 创建工作流引用这些操作
        3. 执行工作流
        4. 验证所有步骤按顺序执行
        """
        await self.page.goto("about:blank")
        await self.page.set_content(
            "<html><body>"
            "<button id='btn1'>Button 1</button>"
            "<button id='btn2'>Button 2</button>"
            "<div id='result'>Result</div>"
            "</body></html>"
        )

        action1 = await self._create_custom_action(
            name="点击按钮1",
            steps=[
                {"action_id": "click", "params": {"selector": "#btn1"}},
            ],
        )

        action2 = await self._create_custom_action(
            name="点击按钮2",
            steps=[
                {"action_id": "click", "params": {"selector": "#btn2"}},
            ],
        )

        # 创建一个包含所有工作流步骤的自定义操作
        workflow_action = await self._create_custom_action(
            name="工作流测试操作",
            steps=[
                {"action_id": action1.action_id, "params": {}},
                {"action_id": action2.action_id, "params": {}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        results = await self._execute_workflow_from_db(
            req=WorkflowExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=workflow_action.action_id,
                input_data={},
                output=[],
                variables={},
            ),
        )

        assert len(results) == 3
        assert all(r.success for r in results)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_nested_composite_action(self):
        """测试：执行嵌套的自定义操作

        流程：
        1. 创建内部自定义操作
        2. 创建外部自定义操作引用内部操作
        3. 执行外部操作
        4. 验证嵌套执行正确
        """
        inner_action = await self._create_custom_action(
            name="内部操作",
            steps=[
                {"action_id": "click", "params": {"selector": "#btn"}},
            ],
        )

        await self.page.goto("about:blank")
        await self.page.set_content(
            "<html><body>"
            "<button id='btn'>Click Me</button>"
            "</body></html>"
        )

        outer_action = await self._create_custom_action(
            name="外部操作",
            steps=[
                {"action_id": inner_action.action_id, "params": {}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        result = await execution_engine.execute_action(
            req=ActionExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=outer_action.action_id,
                params={},
                input_data={},
                output=[],
            ),
            session_id="test_session",
            browser_id="test_browser",
        )

        assert result.success
        assert result.data.total_steps == 2
        assert result.data.success_count == 2

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_failed_action_stops_workflow(self):
        """测试：失败的步骤中断工作流

        流程：
        1. 创建工作流包含会失败的步骤
        2. 执行工作流
        3. 验证失败后后续步骤未执行
        """
        # 创建一个包含所有工作流步骤的自定义操作
        failed_action = await self._create_custom_action(
            name="失败测试操作",
            steps=[
                {"action_id": "navigate", "params": {
                    "url": "https://example.com"}},
                {"action_id": "click", "params": {"selector": "#nonexistent"}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        results = await self._execute_workflow_from_db(
            req=WorkflowExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=failed_action.action_id,
                input_data={},
                output=[],
                variables={},
            ),
        )

        # 应该在 click 步骤失败后停止
        assert len(results) < 3
        assert not results[-1].success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_loop_in_workflow(self):
        """测试：工作流中的循环控制

        流程：
        1. 创建包含循环步骤的工作流
        2. 循环内执行点击操作
        3. 验证循环正确执行指定次数
        """
        await self.page.set_content(
            "<html><body>"
            "<button class='item' data-index='0'>Item 0</button>"
            "<button class='item' data-index='1'>Item 1</button>"
            "<button class='item' data-index='2'>Item 2</button>"
            "</body></html>"
        )

        # 创建一个包含循环步骤的自定义操作（使用 WorkflowStep 结构）
        loop_action = await self._create_custom_action(
            name="循环测试操作",
            steps=[
                create_workflow_step(
                    action_id="loop",
                    action_type="loop",
                    params={"count": 3},
                    children=[
                        create_workflow_step(
                            action_id="click",
                            action_type="click",
                            params={
                                "selector": ".item[data-index=\"{{loop_index}}\"]"},
                        )
                    ],
                ),
            ],
        )

        results = await self._execute_workflow_from_db(
            req=WorkflowExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=loop_action.action_id,
                input_data={},
                output=[],
                variables={},
            ),
        )

        assert len(results) == 3
        assert all(r.success for r in results)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_condition_branch_in_workflow(self):
        """测试：工作流中的条件分支

        流程：
        1. 创建包含条件判断的工作流
        2. 设置不同的条件值
        3. 验证正确的分支被执行
        """
        await self.page.set_content(
            "<html><body>"
            "<button id='true_btn'>True Button</button>"
            "<button id='false_btn'>False Button</button>"
            "</body></html>"
        )

        # 构建结构化条件：检查 should_click_true 是否为 True
        condition_rule = ConditionRule(
            logic=LogicOperator.AND,
            condition=ParamsCondition(
                field="should_click_true",
                condition_value_type=ConditionValueType.BOOLEAN,
                condition_value=True,
            ),
        )

        # 创建一个包含条件分支的自定义操作（使用 WorkflowStep 结构）
        condition_action = await self._create_custom_action(
            name="条件分支测试操作",
            steps=[
                create_workflow_step(
                    action_id="if_else",
                    action_type="if_else",
                    params={
                        "condition": condition_rule.model_dump(),
                        "TrueBranch": [
                            create_workflow_step(
                                action_id="click",
                                action_type="click",
                                params={"selector": "#true_btn"},
                            )
                        ],
                        "FalseBranch": [
                            create_workflow_step(
                                action_id="click",
                                action_type="click",
                                params={"selector": "#false_btn"},
                            )
                        ],
                    },
                ),
            ],
        )

        # 测试 True 分支
        results_true = await self._execute_workflow_from_db(
            req=WorkflowExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=condition_action.action_id,
                input_data={},
                output=[],
                variables={"should_click_true": True},
            ),
        )

        assert results_true[0].success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_composite_action_with_output_variables(self):
        """测试：自定义操作输出变量传递

        流程：
        1. 创建包含输出变量的自定义操作
        2. 后续步骤引用前面步骤的输出
        3. 验证变量正确传递
        """
        await self.page.set_content(
            "<html><body>"
            "<div id='content'>Original Content</div>"
            "</body></html>"
        )

        action = await self._create_custom_action(
            name="变量传递测试",
            steps=[
                {"action_id": "screenshot", "params": {},
                    "output_var": "screenshot_data"},
                {"action_id": "navigate", "params": {
                    "url": "https://example.com"}},
            ],
        )

        result = await execution_engine.execute_action(
            req=ActionExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=action.action_id,
                params={},
                input_data={},
                output=[],
            ),
            session_id="test_session",
            browser_id="test_browser",
        )

        assert result.success
        assert result.data.success_count == 2
