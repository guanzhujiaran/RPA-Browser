"""
测试控制流类 Action - Loop, IfElse, Composite
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestLoopAction:
    """循环控制流操作测试"""
    
    @pytest.mark.asyncio
    async def test_loop_with_count(self, mock_action_context):
        """测试固定次数循环"""
        from app.services.execution.actions.control_flow import LoopAction
        
        action = LoopAction()
        
        async def mock_execute_steps(steps, ctx):
            return [MagicMock(success=True) for _ in steps]
        
        mock_action_context.params = {"count": 3}
        mock_action_context.user_data = {
            "_execute_steps_func": mock_execute_steps,
            "_children_steps": [{"action_id": "click", "params": {"selector": "#btn"}}],
        }
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["iterations"] == 3
    
    @pytest.mark.asyncio
    async def test_loop_with_items(self, mock_action_context):
        """测试遍历列表"""
        from app.services.execution.actions.control_flow import LoopAction
        
        action = LoopAction()
        
        async def mock_execute_steps(steps, ctx):
            return [MagicMock(success=True) for _ in steps]
        
        mock_action_context.params = {"items": ["a", "b", "c"]}
        mock_action_context.user_data = {
            "_execute_steps_func": mock_execute_steps,
            "_children_steps": [{"action_id": "click", "params": {"selector": "#btn"}}],
        }
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["iterations"] == 3
    
    @pytest.mark.asyncio
    async def test_loop_without_execute_func(self, mock_action_context):
        """测试没有 execute_steps_func 的情况"""
        from app.services.execution.actions.control_flow import LoopAction
        
        action = LoopAction()
        mock_action_context.params = {"count": 2}
        mock_action_context.user_data = {}
        
        result = await action.execute(mock_action_context)
        
        assert not result.success
        assert "必须在 Workflow 上下文中执行" in result.error
    
    @pytest.mark.asyncio
    async def test_loop_without_children(self, mock_action_context):
        """测试没有子步骤的情况"""
        from app.services.execution.actions.control_flow import LoopAction
        
        action = LoopAction()
        
        async def mock_execute_steps(steps, ctx):
            return []
        
        mock_action_context.params = {"count": 2}
        mock_action_context.user_data = {
            "_execute_steps_func": mock_execute_steps,
            "_children_steps": [],
        }
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert "无子步骤可执行" in result.data["message"]


class TestIfElseAction:
    """条件分支控制流操作测试"""
    
    @pytest.mark.asyncio
    async def test_if_else_true_branch(self, mock_action_context):
        """测试条件为真时执行 true_branch"""
        from app.services.execution.actions.control_flow import IfElseAction
        
        action = IfElseAction()
        
        async def mock_execute_steps(steps, ctx):
            return [MagicMock(success=True)]
        
        mock_action_context.params = {"condition": "state['value'] > 5"}
        mock_action_context.user_data = {
            "_execute_steps_func": mock_execute_steps,
            "_true_branch_steps": [{"action_id": "click"}],
            "_false_branch_steps": [{"action_id": "wait"}],
            "state": {"value": 10},
        }
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["branch_taken"] == "true_branch"
    
    @pytest.mark.asyncio
    async def test_if_else_false_branch(self, mock_action_context):
        """测试条件为假时执行 false_branch"""
        from app.services.execution.actions.control_flow import IfElseAction
        
        action = IfElseAction()
        
        async def mock_execute_steps(steps, ctx):
            return [MagicMock(success=True)]
        
        mock_action_context.params = {"condition": "state['value'] > 5"}
        mock_action_context.user_data = {
            "_execute_steps_func": mock_execute_steps,
            "_true_branch_steps": [{"action_id": "click"}],
            "_false_branch_steps": [{"action_id": "wait"}],
            "state": {"value": 3},
        }
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert result.data["branch_taken"] == "false_branch"
    
    @pytest.mark.asyncio
    async def test_if_else_without_execute_func(self, mock_action_context):
        """测试没有 execute_steps_func 的情况"""
        from app.services.execution.actions.control_flow import IfElseAction
        
        action = IfElseAction()
        mock_action_context.params = {"condition": "True"}
        mock_action_context.user_data = {}
        
        result = await action.execute(mock_action_context)
        
        assert not result.success
        assert "必须在 Workflow 上下文中执行" in result.error
    
    @pytest.mark.asyncio
    async def test_if_else_without_children(self, mock_action_context):
        """测试分支无步骤的情况"""
        from app.services.execution.actions.control_flow import IfElseAction
        
        action = IfElseAction()
        
        async def mock_execute_steps(steps, ctx):
            return []
        
        mock_action_context.params = {"condition": "True"}
        mock_action_context.user_data = {
            "_execute_steps_func": mock_execute_steps,
            "_true_branch_steps": [],
            "_false_branch_steps": [],
            "state": {},
        }
        
        result = await action.execute(mock_action_context)
        
        assert result.success
        assert "分支无步骤" in result.data["message"]


class TestCompositeAction:
    """组合操作测试"""
    
    @pytest.mark.asyncio
    async def test_composite_action_execution(self, mock_action_context):
        """测试组合动作执行"""
        from app.services.execution.actions.control_flow import CompositeAction
        
        class TestComposite(CompositeAction):
            @staticmethod
            def get_action_id() -> str:
                return "test_composite"
        
        mock_registry = MagicMock()
        mock_sub_action = MagicMock()
        mock_sub_action.execute = AsyncMock(return_value=MagicMock(success=True))
        mock_registry.create_action.return_value = mock_sub_action
        
        composite = TestComposite(
            action_id="test_composite",
            name="测试组合",
            description="测试",
            steps=[{"action_id": "click", "params": {"selector": "#btn"}}],
        )
        composite.set_registry(mock_registry)
        
        result = await composite.execute(mock_action_context)
        
        assert result.success
        assert result.data["steps_count"] == 1
    
    @pytest.mark.asyncio
    async def test_composite_action_without_registry(self, mock_action_context):
        """测试没有注册表的情况"""
        from app.services.execution.actions.control_flow import CompositeAction
        
        class TestComposite(CompositeAction):
            @staticmethod
            def get_action_id() -> str:
                return "test_composite"
        
        composite = TestComposite(
            action_id="test_composite",
            name="测试组合",
            description="测试",
            steps=[{"action_id": "click"}],
        )
        
        result = await composite.execute(mock_action_context)
        
        assert not result.success
        assert "操作注册表未初始化" in result.error
    
    @pytest.mark.asyncio
    async def test_composite_action_circular_reference(self, mock_action_context):
        """测试循环引用检测"""
        from app.services.execution.actions.control_flow import CompositeAction
        
        class TestComposite(CompositeAction):
            @staticmethod
            def get_action_id() -> str:
                return "test_composite"
        
        mock_registry = MagicMock()
        mock_sub_action = MagicMock()
        mock_sub_action.execute = AsyncMock(return_value=MagicMock(success=True))
        mock_registry.create_action.return_value = mock_sub_action
        
        composite = TestComposite(
            action_id="test_composite",
            name="测试组合",
            description="测试",
            steps=[{"action_id": "test_composite"}],
        )
        composite.set_registry(mock_registry)
        
        result = await composite.execute(mock_action_context)
        
        assert result.success
    
    @pytest.mark.asyncio
    async def test_composite_action_step_failure(self, mock_action_context):
        """测试子步骤失败"""
        from app.services.execution.actions.control_flow import CompositeAction
        
        class TestComposite(CompositeAction):
            @staticmethod
            def get_action_id() -> str:
                return "test_composite"
        
        mock_registry = MagicMock()
        mock_sub_action = MagicMock()
        mock_sub_action.execute = AsyncMock(return_value=MagicMock(success=False, error="step failed"))
        mock_registry.create_action.return_value = mock_sub_action
        
        composite = TestComposite(
            action_id="test_composite",
            name="测试组合",
            description="测试",
            steps=[{"action_id": "click", "params": {"selector": "#btn"}}],
        )
        composite.set_registry(mock_registry)
        
        result = await composite.execute(mock_action_context)
        
        assert not result.success