"""
组合 Action 执行测试

测试内容：
1. LoopAction - 循环执行
2. IfElseAction - 条件分支
3. CompositeAction - 组合动作（从数据库加载）
4. 嵌套组合动作
"""

import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.execution.actions.control_flow import LoopAction, IfElseAction, CompositeAction
from app.services.execution.actions.interaction import ClickAction
from app.models.database.workflow.models import ActionContext, ActionResult


class TestLoopAction:
    """循环操作测试"""
    
    @pytest.mark.asyncio
    async def test_loop_count(self, mock_page, mock_browser):
        """测试固定次数循环"""
        executed_count = 0
        
        async def mock_execute_steps(steps, ctx):
            nonlocal executed_count
            executed_count += 1
            return [ActionResult(success=True, action_id="click")]
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={
                "count": 3,
                "children": [{"action_id": "click", "params": {"selector": "#btn"}}]
            },
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        action = LoopAction()
        result = await action.execute(ctx)
        
        assert result.success is True
        assert executed_count == 3
        assert result.data["iterations"] == 3
    
    @pytest.mark.asyncio
    async def test_loop_items(self, mock_page, mock_browser):
        """测试遍历列表循环"""
        items = ["item1", "item2", "item3"]
        collected_items = []
        
        async def mock_execute_steps(steps, ctx):
            collected_items.append(ctx.user_data.get("loop_item"))
            return [ActionResult(success=True, action_id="click")]
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={
                "items": items,
                "children": [{"action_id": "click", "params": {"selector": "#btn"}}]
            },
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        action = LoopAction()
        result = await action.execute(ctx)
        
        assert result.success is True
        assert collected_items == items
    
    @pytest.mark.asyncio
    async def test_loop_empty(self, mock_page, mock_browser):
        """测试空循环"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={
                "count": 0,
                "children": [{"action_id": "click", "params": {"selector": "#btn"}}]
            },
            user_data={}
        )
        
        action = LoopAction()
        result = await action.execute(ctx)
        
        assert result.success is True
        assert result.data["iterations"] == 0


class TestIfElseAction:
    """条件分支测试"""
    
    @pytest.mark.asyncio
    async def test_if_true_branch(self, mock_page, mock_browser):
        """测试条件为真时执行 true 分支"""
        executed_branch = None
        
        async def mock_execute_steps(steps, ctx):
            nonlocal executed_branch
            executed_branch = "true"
            return [ActionResult(success=True, action_id="click")]
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={
                "condition": "True",
                "true_branch": [{"action_id": "click", "params": {"selector": "#btn"}}],
                "false_branch": [{"action_id": "input", "params": {"selector": "#input", "value": "test"}}]
            },
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        action = IfElseAction()
        result = await action.execute(ctx)
        
        assert result.success is True
        assert executed_branch == "true"
    
    @pytest.mark.asyncio
    async def test_if_false_branch(self, mock_page, mock_browser):
        """测试条件为假时执行 false 分支"""
        executed_branch = None
        
        async def mock_execute_steps(steps, ctx):
            nonlocal executed_branch
            executed_branch = "false"
            return [ActionResult(success=True, action_id="input")]
        
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={
                "condition": "False",
                "true_branch": [{"action_id": "click", "params": {"selector": "#btn"}}],
                "false_branch": [{"action_id": "input", "params": {"selector": "#input", "value": "test"}}]
            },
            user_data={"_execute_steps_func": mock_execute_steps}
        )
        
        action = IfElseAction()
        result = await action.execute(ctx)
        
        assert result.success is True
        assert executed_branch == "false"
    
    @pytest.mark.asyncio
    async def test_if_with_variables(self, mock_page, mock_browser):
        """测试使用变量的条件"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            params={
                "condition": "state.get('count', 0) > 5",
                "true_branch": [{"action_id": "click"}],
                "false_branch": [{"action_id": "input"}]
            },
            user_data={"state": {"count": 10}, "_execute_steps_func": AsyncMock(return_value=[ActionResult(success=True)])}
        )
        
        action = IfElseAction()
        result = await action.execute(ctx)
        
        assert result.success is True


class TestCompositeAction:
    """组合动作测试"""
    
    @pytest.mark.asyncio
    async def test_composite_sequence(self, mock_page, mock_browser):
        """测试顺序执行多个动作"""
        executed_actions = []
        
        async def mock_execute_steps(steps, ctx):
            for step in steps:
                executed_actions.append(step.get("action_id"))
            return [
                ActionResult(success=True, action_id="navigate"),
                ActionResult(success=True, action_id="click"),
                ActionResult(success=True, action_id="input")
            ]
        
        steps = [
            {"action_id": "navigate", "params": {"url": "https://example.com"}},
            {"action_id": "click", "params": {"selector": "#btn"}},
            {"action_id": "input", "params": {"selector": "#input", "value": "test"}}
        ]
        
        action = CompositeAction(
            action_id="ca_login",
            name="登录流程",
            description="完整的登录流程",
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
        
        result = await action.execute(ctx)
        
        assert result.success is True
        assert result.data["total_steps"] == 3
        assert result.data["success_count"] == 3
    
    @pytest.mark.asyncio
    async def test_composite_partial_failure(self, mock_page, mock_browser):
        """测试部分失败的组合动作"""
        async def mock_execute_steps(steps, ctx):
            return [
                ActionResult(success=True, action_id="navigate"),
                ActionResult(success=False, action_id="click", error="Element not found"),
                ActionResult(success=True, action_id="input")  # 不会执行到这里
            ]
        
        steps = [
            {"action_id": "navigate"},
            {"action_id": "click"},
            {"action_id": "input"}
        ]
        
        action = CompositeAction(
            action_id="ca_test",
            name="测试流程",
            description="测试流程",
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
        
        result = await action.execute(ctx)
        
        assert result.success is False
        assert result.error is not None


class TestNestedComposite:
    """嵌套组合动作测试"""
    
    @pytest.mark.asyncio
    async def test_nested_loop_in_composite(self, mock_page, mock_browser):
        """测试组合动作中包含循环"""
        execution_log = []
        
        async def mock_execute_steps(steps, ctx):
            for step in steps:
                execution_log.append(step.get("action_id"))
            return [ActionResult(success=True, action_id=step.get("action_id")) for step in steps]
        
        # 组合动作：导航 -> 循环点击3次 -> 输入
        steps = [
            {"action_id": "navigate", "params": {"url": "https://example.com"}},
            {
                "action_id": "loop",
                "params": {
                    "count": 3,
                    "children": [{"action_id": "click", "params": {"selector": "#item"}}]
                }
            },
            {"action_id": "input", "params": {"selector": "#result", "value": "done"}}
        ]
        
        action = CompositeAction(
            action_id="ca_nested",
            name="嵌套测试",
            description="嵌套测试",
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
        
        result = await action.execute(ctx)
        
        assert result.success is True
        # 验证执行了 navigate、loop、input
        assert "navigate" in execution_log
        assert "loop" in execution_log
        assert "input" in execution_log
