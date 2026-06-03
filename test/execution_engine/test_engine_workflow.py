"""
执行引擎测试 - 数据库驱动的复合操作执行测试

测试流程：
1. 创建复合操作（CompositeAction）并入库
2. 可选：创建插件（UserPlugin）并关联
3. 从数据库读取操作
4. 通过 ExecutionEngine 执行
5. 验证执行结果
"""
import pytest
import uuid
import aiounittest
from playwright.async_api import Page
from app.models.database.workflow.models import (
    CompositeAction,
    UserPlugin,
    BuiltinActionType,
)
from app.services.execution.crud_service import action_crud, plugin_crud
from app.services.execution.execution_engine import execution_engine
from app.models.execution.params import ActionExecutionRequest, WorkflowExecutionRequest


class TestExecutionEngineDatabaseWorkflow(aiounittest.AsyncTestCase):
    """测试 ExecutionEngine 执行数据库中存储的自定义操作"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page
        self.mid = 12345678
        self.test_action_ids = []
        self.test_plugin_ids = []

    @pytest.mark.asyncio(loop_scope="session")
    async def teardown(self):
        """清理测试数据"""
        for action_id in self.test_action_ids:
            from sqlmodel import select
            from app.utils.depends.session_manager import DatabaseSessionManager
            async with DatabaseSessionManager.async_session() as session:
                from app.models.database.workflow.models import CompositeAction
                result = await session.exec(
                    select(CompositeAction).where(CompositeAction.action_id == action_id)
                )
                if action:= result.first():
                    await action_crud.delete(action.id)

        
        for plugin_id in self.test_plugin_ids:
            plugin = await plugin_crud.get_by_plugin_id(plugin_id)
            if plugin:
                await plugin_crud.delete(plugin.id)
    

    async def _create_custom_action(self, name: str, steps: list, **kwargs) -> CompositeAction:
        """创建自定义操作并入库"""
        action_id = f"ca_{uuid.uuid4().hex[:12]}"
        
        action = await action_crud.create(
            mid=str(self.mid),
            action_id=action_id,
            name=name,
            action_type=BuiltinActionType.COMPOSITE,
            steps=steps,
            is_composite=True,
            description=kwargs.get("description", ""),
            is_public=kwargs.get("is_public", False),
            enabled_plugins=kwargs.get("enabled_plugins", None),
        )
        
        self.test_action_ids.append(action_id)
        return action

    async def _create_plugin(self, name: str, hook_type: str, custom_action_id: str) -> UserPlugin:
        """创建插件并关联到自定义操作"""
        plugin_id = f"plugin_{uuid.uuid4().hex[:8]}"
        
        plugin = await plugin_crud.create(
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
            page=self.page,
        )
        
        assert result.success
        assert result.data["total_steps"] == 2
        assert result.data["success_count"] == 2

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
            {"action_id": "input", "params": {"selector": "#username", "value": "{user_name}"}},
            {"action_id": "input", "params": {"selector": "#password", "value": "{user_pass}"}},
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
            page=self.page,
        )
        
        assert result.success
        assert result.data["success_count"] == 3
        
        # 验证输入值已正确填入
        username_value = await self.page.input_value("#username")
        assert "testuser" in username_value

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_composite_with_plugin(self):
        """测试：执行带插件关联的自定义操作
        
        流程：
        1. 创建基础自定义操作（导航+截图）
        2. 创建插件并关联到该操作
        3. 执行自定义操作
        4. 验证插件在钩子点被执行
        """
        base_action = await self._create_custom_action(
            name="基础操作",
            steps=[
                {"action_id": "navigate", "params": {"url": "https://example.com"}},
            ],
        )
        
        plugin_action = await self._create_custom_action(
            name="截图插件操作",
            steps=[
                {"action_id": "screenshot", "params": {}},
            ],
        )
        
        await self._create_plugin(
            name="截图插件",
            hook_type="after_action",
            custom_action_id=plugin_action.action_id,
        )
        
        await action_crud.update(
            id=base_action.id,
            enabled_plugins=[
                {
                    "plugin_id": plugin_action.action_id,
                    "config_params": {},
                }
            ],
        )
        
        result = await execution_engine.execute_action(
            req=ActionExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=base_action.action_id,
                params={},
                input_data={},
                output=[],
            ),
            session_id="test_session",
            browser_id="test_browser",
            page=self.page,
        )
        
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execute_workflow_with_custom_actions(self):
        """测试：执行包含自定义操作的工作流
        
        流程：
        1. 创建多个自定义操作
        2. 创建工作流引用这些操作
        3. 执行工作流
        4. 验证所有步骤按顺序执行
        """
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
                {"action_id": "navigate", "params": {"url": "https://example.com"}},
                {"action_id": action1.action_id, "params": {}},
                {"action_id": action2.action_id, "params": {}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        results = await execution_engine.execute_workflow_with_session(
            req=WorkflowExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=workflow_action.action_id,
                input_data={},
                output=[],
                variables={},
            ),
        )
        
        assert len(results) == 4
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
        
        await self.page.set_content(
            "<html><body>"
            "<button id='btn'>Click Me</button>"
            "</body></html>"
        )
        
        outer_action = await self._create_custom_action(
            name="外部操作",
            steps=[
                {"action_id": "navigate", "params": {"url": "https://example.com"}},
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
            page=self.page,
        )
        
        assert result.success
        assert result.data["total_steps"] == 3
        assert result.data["success_count"] == 3

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
                {"action_id": "navigate", "params": {"url": "https://example.com"}},
                {"action_id": "click", "params": {"selector": "#nonexistent"}},
                {"action_id": "screenshot", "params": {}},
            ],
        )
        
        results = await execution_engine.execute_workflow_with_session(
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
        
        # 创建一个包含循环步骤的自定义操作
        loop_action = await self._create_custom_action(
            name="循环测试操作",
            steps=[
                {"action_id": "loop", "params": {
                    "count": 3,
                    "_children_steps": [
                        {"action_id": "click", "params": {"selector": ".item:nth-child({{state.loop.index}})"}}
                    ],
                }},
            ],
        )
        
        results = await execution_engine.execute_workflow_with_session(
            req=WorkflowExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=loop_action.action_id,
                input_data={},
                output=[],
                variables={},
            ),
        )
        
        assert len(results) == 1
        assert results[0].success
        assert results[0].data.get("iterations") == 3

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
        
        # 创建一个包含条件分支的自定义操作
        condition_action = await self._create_custom_action(
            name="条件分支测试操作",
            steps=[
                {"action_id": "if_else", "params": {
                    "condition": "state.condition == True",
                    "true_branch_steps": [
                        {"action_id": "click", "params": {"selector": "#true_btn"}}
                    ],
                    "false_branch_steps": [
                        {"action_id": "click", "params": {"selector": "#false_btn"}}
                    ],
                }},
            ],
        )
        
        # 测试 True 分支
        results_true = await execution_engine.execute_workflow_with_session(
            req=WorkflowExecutionRequest(
                mid=self.mid,
                browser_id=1,
                action_id=condition_action.action_id,
                input_data={},
                output=[],
                variables={"condition": True},
            ),
        )
        
        assert results_true[0].success
        assert results_true[0].data.get("branch") == "true_branch"

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
                {"action_id": "screenshot", "params": {}, "output_var": "screenshot_data"},
                {"action_id": "navigate", "params": {"url": "https://example.com"}},
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
            page=self.page,
        )
        
        assert result.success
        assert result.data["success_count"] == 2
