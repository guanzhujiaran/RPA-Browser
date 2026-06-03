"""
组合测试 - 自定义操作全流程测试
测试：创建自定义操作（含子步骤和变量）→ 通过 ExecutionEngine 执行
使用 aiounittest.AsyncTestCase 模式
"""
import pytest
import aiounittest
from playwright.async_api import Page
from loguru import logger

class TestCompositeActionWorkflow(aiounittest.AsyncTestCase):
    """测试自定义组合操作全流程（不 mock，直接执行真实步骤）"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_composite_navigate_and_screenshot(self):
        """测试：导航 + 截图 组合执行"""
        from app.services.execution.actions.base import CompositeAction
        logger.info("开始测试：导航 + 截图 组合执行")
        action = CompositeAction(
            page=self.page,
            params={},
            variables={},
            steps=[
                {"action_id": "navigate", "params": {"url": "https://example.com"}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        result = await action.execute()

        assert result.success
        assert result.data["total_steps"] == 2
        assert result.data["success_count"] == 2
        assert "png" in result.data["details"][1]["data"].get("format", "")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_composite_navigate_click_screenshot(self):
        """测试：导航 → 点击 → 截图 完整流程"""
        from app.services.execution.actions.base import CompositeAction
        logger.info("开始测试：导航 → 点击 → 截图 完整流程")
        
        # 先导航到 example.com
        action = CompositeAction(
            page=self.page,
            params={},
            variables={},
            steps=[
                {"action_id": "navigate", "params": {"url": "https://example.com"}},
            ],
        )
        result = await action.execute()
        assert result.success

        # 设置页面内容模拟可交互页面
        await self.page.set_content(
            "<html><body>"
            "<button id='btn'>Click Me</button>"
            "<div id='result' style='width: 100px; height: 50px;'>Result</div>"
            "</body></html>"
        )

        action2 = CompositeAction(
            page=self.page,
            params={},
            variables={},
            steps=[
                {"action_id": "click", "params": {"selector": "#btn"}},
                {"action_id": "screenshot", "params": {}},
            ],
        )
        result2 = await action2.execute()

        assert result2.success
        assert result2.data["total_steps"] == 2
        assert result2.data["success_count"] == 2

    @pytest.mark.asyncio(loop_scope="session")
    async def test_composite_with_variable_injection(self):
        """测试：变量注入到步骤参数"""
        from app.services.execution.actions.base import CompositeAction
        logger.info("开始测试：变量注入到步骤参数")
        
        # 设置页面
        await self.page.set_content(
            "<html><body>"
            "<input id='username' type='text'>"
            "<input id='email' type='email'>"
            "<button id='submit'>Submit</button>"
            "</body></html>"
        )

        action = CompositeAction(
            page=self.page,
            params={},
            variables={
                "target_url": "https://example.com",
                "user_name": "test_user_123",
                "email_addr": "test@example.com",
                "selector_submit": "#submit",
            },
            steps=[
                {"action_id": "input", "params": {"selector": "#username", "value": "{user_name}"}},
                {"action_id": "input", "params": {"selector": "#email", "value": "{email_addr}"}},
                {"action_id": "click", "params": {"selector": "{selector_submit}"}},
            ],
        )

        result = await action.execute()

        assert result.success
        assert result.data["success_count"] == 3

    @pytest.mark.asyncio(loop_scope="session")
    async def test_composite_input_and_verify(self):
        """测试：输入 → 验证输入结果"""
        from app.services.execution.actions.base import CompositeAction
        logger.info("开始测试：输入 → 验证输入结果")
        
        await self.page.set_content(
            "<html><body>"
            "<input id='input_field' type='text'>"
            "<div id='display'></div>"
            "</body></html>"
        )

        action = CompositeAction(
            page=self.page,
            params={},
            variables={"input_value": "Hello Playwright!"},
            steps=[
                {"action_id": "input", "params": {"selector": "#input_field", "value": "{input_value}"}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        result = await action.execute()

        assert result.success
        assert result.data["success_count"] == 2

        # 验证输入值已填入
        value = await self.page.input_value("#input_field")
        assert "Hello Playwright!" in value

    @pytest.mark.asyncio(loop_scope="session")
    async def test_composite_scroll_and_screenshot(self):
        """测试：滚动 + 截图"""
        from app.services.execution.actions.base import CompositeAction
        logger.info("开始测试：滚动 + 截图")
        
        await self.page.set_content(
            "<html><body>"
            "<div style='height: 50px;'>Header</div>"
            "<div id='content' style='height: 2000px;'>Long Content</div>"
            "<div id='footer' style='height: 50px;'>Footer</div>"
            "</body></html>"
        )

        action = CompositeAction(
            page=self.page,
            params={},
            variables={},
            steps=[
                {"action_id": "scroll", "params": {}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        result = await action.execute()

        assert result.success
        assert result.data["success_count"] == 2

    @pytest.mark.asyncio(loop_scope="session")
    async def test_composite_multiple_inputs(self):
        """测试：多输入框填写"""
        from app.services.execution.actions.base import CompositeAction
        logger.info("开始测试：多输入框填写")
        
        await self.page.set_content(
            "<html><body>"
            "<form>"
            "<input id='name' type='text'>"
            "<input id='age' type='number'>"
            "<input id='city' type='text'>"
            "<button id='submit_form'>Submit</button>"
            "</form>"
            "</body></html>"
        )

        action = CompositeAction(
            page=self.page,
            params={},
            variables={"user_name": "张三", "user_age": "25", "user_city": "北京"},
            steps=[
                {"action_id": "input", "params": {"selector": "#name", "value": "{user_name}"}},
                {"action_id": "input", "params": {"selector": "#age", "value": "{user_age}"}},
                {"action_id": "input", "params": {"selector": "#city", "value": "{user_city}"}},
                {"action_id": "click", "params": {"selector": "#submit_form"}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        result = await action.execute()

        assert result.success
        assert result.data["total_steps"] == 5
        assert result.data["success_count"] == 5

    @pytest.mark.asyncio(loop_scope="session")
    async def test_composite_hover_and_click(self):
        """测试：悬停 + 点击"""
        from app.services.execution.actions.base import CompositeAction
        logger.info("开始测试：悬停 + 点击")
        
        await self.page.set_content(
            "<html><body>"
            "<div id='menu' style='width: 100px; height: 50px; background: blue;'>Menu</div>"
            "<button id='menu_item' style='display: none;'>Item</button>"
            "</body></html>"
        )

        action = CompositeAction(
            page=self.page,
            params={},
            variables={},
            steps=[
                {"action_id": "hover", "params": {"selector": "#menu"}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        result = await action.execute()

        assert result.success
        assert result.data["success_count"] == 2

    @pytest.mark.asyncio(loop_scope="session")
    async def test_composite_failed_step_stops_execution(self):
        """测试：单步失败中断后续操作"""
        from app.services.execution.actions.base import CompositeAction
        logger.info("开始测试：单步失败中断后续操作")
        
        await self.page.set_content("<html><body><div>Content</div></body></html>")

        action = CompositeAction(
            page=self.page,
            params={},
            variables={},
            steps=[
                {"action_id": "navigate", "params": {"url": "https://example.com"}},
                {"action_id": "click", "params": {"selector": "#nonexistent"}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        result = await action.execute()

        # 应该失败，且不是所有步骤都执行成功
        assert not result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_composite_full_login_simulation(self):
        """测试：模拟完整登录流程"""
        from app.services.execution.actions.control_flow import CompositeAction
        logger.info("开始测试：模拟完整登录流程")
        
        # 步骤 1: 导航到登录页
        await self.page.set_content(
            "<html><body>"
            "<h1>Login Page</h1>"
            "<form>"
            "<input id='username' type='text' placeholder='Username'>"
            "<input id='password' type='password' placeholder='Password'>"
            "<button id='login_btn'>Login</button>"
            "</form>"
            "<div id='dashboard' style='display: none;'>Welcome to Dashboard</div>"
            "</body></html>"
        )

        action = CompositeAction(
            page=self.page,
            params={},
            variables={
                "login_user": "admin",
                "login_pass": "password123",
            },
            steps=[
                {"action_id": "input", "params": {"selector": "#username", "value": "{login_user}"}},
                {"action_id": "input", "params": {"selector": "#password", "value": "{login_pass}"}},
                {"action_id": "click", "params": {"selector": "#login_btn"}},
                {"action_id": "screenshot", "params": {}},
            ],
        )

        result = await action.execute()

        assert result.success
        assert result.data["success_count"] == 4
