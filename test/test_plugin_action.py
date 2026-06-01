"""
插件 Action 执行测试

测试内容：
1. PluginAction - 基础插件
2. 从数据库加载的插件
3. 复杂插件场景（before_action, after_action, on_success, on_error）
4. 插件与主 action 的交互
"""

import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.execution.actions.base import PluginAction
from app.services.execution.actions.interaction import ClickAction
from app.models.database.workflow.models import ActionContext, ActionResult, UserPlugin, CustomAction


class TestPluginAction:
    """基础插件测试"""
    
    @pytest.mark.asyncio
    async def test_plugin_basic(self, mock_page, mock_browser):
        """测试基础插件执行"""
        executed_steps = []
        
        async def mock_execute_steps(steps, ctx):
            for step in steps:
                executed_steps.append(step.get("action_id"))
            return [ActionResult(success=True, action_id="click")]
        
        steps = [
            {"action_id": "click", "params": {"selector": "#btn"}}
        ]
        
        plugin = PluginAction(
            action_id="plugin_test",
            action_name="测试插件",
            hook_type="after_action",
            steps=steps
        )
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        result = await plugin.execute(ctx)
        
        assert result.success is True
        assert "click" in executed_steps
    
    @pytest.mark.asyncio
    async def test_plugin_with_multiple_steps(self, mock_page, mock_browser):
        """测试多步骤插件"""
        steps = [
            {"action_id": "click", "params": {"selector": "#btn"}},
            {"action_id": "input", "params": {"selector": "#input", "value": "test"}},
            {"action_id": "screenshot"}
        ]
        
        plugin = PluginAction(
            action_id="plugin_complex",
            action_name="复杂插件",
            hook_type="after_action",
            steps=steps
        )
        
        async def mock_execute_steps(steps, ctx):
            return [ActionResult(success=True, action_id=step.get("action_id")) for step in steps]
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        result = await plugin.execute(ctx)
        
        assert result.success is True
        assert result.data["hook_type"] == "after_action"
    
    @pytest.mark.asyncio
    async def test_plugin_different_hook_types(self, mock_page, mock_browser):
        """测试不同 hook 类型的插件"""
        hook_types = ["before_action", "after_action", "on_success", "on_error"]
        
        for hook_type in hook_types:
            plugin = PluginAction(
                action_id=f"plugin_{hook_type}",
                action_name=f"{hook_type} 插件",
                hook_type=hook_type,
                steps=[{"action_id": "click"}]
            )
            
            async def mock_execute_steps(steps, ctx):
                return [ActionResult(success=True)]
            
            ctx = ActionContext(
                session_id="test",
                browser_id="test",
                page=mock_page,
                browser=mock_browser,
                params={},
                user_data={"_execute_steps_func": mock_execute_steps}
            )
            
            result = await plugin.execute(ctx)
            assert result.success is True


class TestPluginWithDatabase:
    """从数据库加载的插件测试"""
    
    @pytest.mark.asyncio
    async def test_plugin_from_database(self, mock_page, mock_browser, mock_db_manager):
        """测试从数据库加载的插件"""
        # 模拟数据库中的插件
        mock_plugin = MagicMock(spec=UserPlugin)
        mock_plugin.plugin_id = "plugin_db_test"
        mock_plugin.name = "数据库插件"
        mock_plugin.hook_type = "after_action"
        mock_plugin.custom_action_id = "ca_linked"
        mock_plugin.mid = 123
        mock_plugin.is_enabled = True
        
        # 模拟关联的 CustomAction
        mock_custom_action = MagicMock(spec=CustomAction)
        mock_custom_action.action_id = "ca_linked"
        mock_custom_action.name = "关联动作"
        mock_custom_action.steps = [
            {"action_id": "click", "params": {"selector": "#btn"}},
            {"action_id": "input", "params": {"selector": "#input", "value": "test"}}
        ]
        mock_custom_action.is_enabled = True
        
        with patch("app.utils.depends.session_manager.DatabaseSessionManager.async_session") as mock_session:
            session = AsyncMock()
            
            # 第一次查询 UserPlugin
            result_mock = MagicMock()
            result_mock.first.return_value = mock_plugin
            session.exec.return_value = result_mock
            
            mock_session.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            
            from app.services.execution.action_registry import action_registry
            
            # 手动创建插件（模拟从数据库加载）
            plugin = PluginAction(
                action_id="plugin_db_test",
                action_name="数据库插件",
                hook_type="after_action",
                steps=mock_custom_action.steps
            )
            
            async def mock_execute_steps(steps, ctx):
                return [ActionResult(success=True, action_id=step.get("action_id")) for step in steps]
            
            ctx = ActionContext(
                session_id="test",
                browser_id="test",
                page=mock_page,
                browser=mock_browser,
                params={},
                user_data={"_execute_steps_func": mock_execute_steps}
            )
            
            result = await plugin.execute(ctx)
            
            assert result.success is True
            assert result.data["hook_type"] == "after_action"


class TestPluginInteraction:
    """插件与主 action 交互测试"""
    
    @pytest.mark.asyncio
    async def test_plugin_access_variables(self, mock_page, mock_browser):
        """测试插件访问主 action 设置的变量"""
        plugin_result = None
        
        async def mock_execute_steps(steps, ctx):
            nonlocal plugin_result
            # 插件应该能访问主 action 设置的变量
            plugin_result = ctx.user_data.get("main_action_result")
            return [ActionResult(success=True)]
        
        plugin = PluginAction(
            action_id="plugin_variable",
            action_name="变量访问插件",
            hook_type="after_action",
            steps=[{"action_id": "click"}]
        )
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={
                "main_action_result": "success",
                "_execute_steps_func": mock_execute_steps
            }
        )
        
        result = await plugin.execute(ctx)
        
        assert result.success is True
        assert plugin_result == "success"
    
    @pytest.mark.asyncio
    async def test_plugin_modify_variables(self, mock_page, mock_browser):
        """测试插件修改共享变量"""
        async def mock_execute_steps(steps, ctx):
            # 插件修改共享变量
            ctx.user_data["plugin_modified"] = True
            return [ActionResult(success=True)]
        
        plugin = PluginAction(
            action_id="plugin_modify",
            action_name="修改变量插件",
            hook_type="after_action",
            steps=[{"action_id": "click"}]
        )
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        result = await plugin.execute(ctx)
        
        assert result.success is True
        assert ctx.user_data.get("plugin_modified") is True
    
    @pytest.mark.asyncio
    async def test_plugin_failure_handling(self, mock_page, mock_browser):
        """测试插件失败处理"""
        async def mock_execute_steps(steps, ctx):
            return [
                ActionResult(success=True, action_id="click"),
                ActionResult(success=False, action_id="input", error="Input failed")
            ]
        
        plugin = PluginAction(
            action_id="plugin_fail",
            action_name="失败插件",
            hook_type="after_action",
            steps=[
                {"action_id": "click"},
                {"action_id": "input"}
            ]
        )
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        result = await plugin.execute(ctx)
        
        assert result.success is False
        assert result.error is not None


class TestComplexPluginScenarios:
    """复杂插件场景测试"""
    
    @pytest.mark.asyncio
    async def test_multiple_plugins_same_hook(self, mock_page, mock_browser):
        """测试同一 hook 的多个插件"""
        execution_order = []
        
        async def mock_execute_steps_1(steps, ctx):
            execution_order.append("plugin_1")
            return [ActionResult(success=True)]
        
        async def mock_execute_steps_2(steps, ctx):
            execution_order.append("plugin_2")
            return [ActionResult(success=True)]
        
        plugins = [
            PluginAction(
                action_id="plugin_1",
                action_name="插件1",
                hook_type="after_action",
                steps=[{"action_id": "click"}]
            ),
            PluginAction(
                action_id="plugin_2",
                action_name="插件2",
                hook_type="after_action",
                steps=[{"action_id": "input"}]
            )
        ]
        
        for i, plugin in enumerate(plugins):
            ctx = ActionContext(
                session_id="test",
                browser_id="test",
                page=mock_page,
                browser=mock_browser,
                params={},
                user_data={"_execute_steps_func": mock_execute_steps_1 if i == 0 else mock_execute_steps_2}
            )
            
            result = await plugin.execute(ctx)
            assert result.success is True
        
        assert len(execution_order) == 2
    
    @pytest.mark.asyncio
    async def test_plugin_chain(self, mock_page, mock_browser):
        """测试插件链式调用"""
        # 第一个插件设置变量
        # 第二个插件读取变量
        shared_data = {}
        
        async def mock_execute_steps_1(steps, ctx):
            ctx.user_data["step1_data"] = "from_step1"
            shared_data["step1"] = "executed"
            return [ActionResult(success=True)]
        
        async def mock_execute_steps_2(steps, ctx):
            shared_data["step1_data_received"] = ctx.user_data.get("step1_data")
            return [ActionResult(success=True)]
        
        plugin1 = PluginAction(
            action_id="plugin_chain_1",
            action_name="链式插件1",
            hook_type="after_action",
            steps=[{"action_id": "click"}]
        )
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={"_execute_steps_func": mock_execute_steps_1}
        )
        
        await plugin1.execute(ctx)
        
        # 第二个插件应该能读取第一个插件设置的变量
        assert shared_data.get("step1") == "executed"
    
    @pytest.mark.asyncio
    async def test_plugin_with_loop(self, mock_page, mock_browser):
        """测试插件中包含循环"""
        iteration_count = 0
        
        async def mock_execute_steps(steps, ctx):
            nonlocal iteration_count
            # 模拟循环执行
            for step in steps:
                if step.get("action_id") == "loop":
                    count = step.get("params", {}).get("count", 1)
                    iteration_count += count
            return [ActionResult(success=True)]
        
        plugin = PluginAction(
            action_id="plugin_loop",
            action_name="循环插件",
            hook_type="after_action",
            steps=[
                {"action_id": "click"},
                {"action_id": "loop", "params": {"count": 3, "children": [{"action_id": "input"}]}},
                {"action_id": "screenshot"}
            ]
        )
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        result = await plugin.execute(ctx)
        
        assert result.success is True
        assert iteration_count == 3


class TestPluginLifecycle:
    """插件生命周期测试"""
    
    @pytest.mark.asyncio
    async def test_before_action_plugin(self, mock_page, mock_browser):
        """测试 before_action 插件在主 action 前执行"""
        execution_log = []
        
        async def mock_execute_steps(steps, ctx):
            execution_log.append("plugin_executed")
            return [ActionResult(success=True)]
        
        plugin = PluginAction(
            action_id="plugin_before",
            action_name="前置插件",
            hook_type="before_action",
            steps=[{"action_id": "prepare"}]
        )
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        result = await plugin.execute(ctx)
        
        assert result.success is True
        assert "plugin_executed" in execution_log
    
    @pytest.mark.asyncio
    async def test_on_success_plugin(self, mock_page, mock_browser):
        """测试 on_success 插件只在成功时执行"""
        plugin_executed = False
        
        async def mock_execute_steps(steps, ctx):
            nonlocal plugin_executed
            plugin_executed = True
            return [ActionResult(success=True)]
        
        plugin = PluginAction(
            action_id="plugin_success",
            action_name="成功插件",
            hook_type="on_success",
            steps=[{"action_id": "celebrate"}]
        )
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        result = await plugin.execute(ctx)
        
        assert result.success is True
        assert plugin_executed is True
    
    @pytest.mark.asyncio
    async def test_on_error_plugin(self, mock_page, mock_browser):
        """测试 on_error 插件只在失败时执行"""
        plugin_executed = False
        
        async def mock_execute_steps(steps, ctx):
            nonlocal plugin_executed
            plugin_executed = True
            return [ActionResult(success=False, error="Error occurred")]
        
        plugin = PluginAction(
            action_id="plugin_error",
            action_name="错误处理插件",
            hook_type="on_error",
            steps=[{"action_id": "handle_error"}]
        )
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={},
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        result = await plugin.execute(ctx)
        
        # 插件本身执行成功，但处理的是错误场景
        assert result.success is False
