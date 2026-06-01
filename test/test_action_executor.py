"""
Action Executor 集成测试

测试内容：
1. 执行器基础功能
2. 步骤列表执行
3. 模板变量替换
4. 循环和条件执行
5. 错误处理和重试
6. 与数据库交互
"""

import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.execution.action_executor import action_executor
from app.services.execution.action_registry import action_registry
from app.services.execution.actions.base import ActionContext
from app.models.database.workflow.models import ActionResult


class TestActionExecutorBasic:
    """执行器基础功能测试"""
    
    @pytest.mark.asyncio
    async def test_execute_single_step(self, mock_page, mock_browser):
        """测试执行单个步骤"""
        steps = [
            {"action_id": "click", "params": {"selector": "#btn"}}
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            mid="123"
        )
        
        assert len(results) == 1
        assert results[0].success is True
    
    @pytest.mark.asyncio
    async def test_execute_multiple_steps(self, mock_page, mock_browser):
        """测试执行多个步骤"""
        steps = [
            {"action_id": "navigate", "params": {"url": "https://example.com"}},
            {"action_id": "click", "params": {"selector": "#btn"}},
            {"action_id": "input", "params": {"selector": "#input", "value": "test"}}
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            mid="123"
        )
        
        assert len(results) == 3
        assert all(r.success for r in results)
    
    @pytest.mark.asyncio
    async def test_execute_with_variables(self, mock_page, mock_browser):
        """测试带变量的执行"""
        variables = {"username": "test_user", "password": "test_pass"}
        
        steps = [
            {"action_id": "input", "params": {"selector": "#user", "value": "{{username}}"}},
            {"action_id": "input", "params": {"selector": "#pass", "value": "{{password}}"}}
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            variables=variables,
            mid="123"
        )
        
        assert len(results) == 2
        assert all(r.success for r in results)


class TestActionExecutorLoop:
    """执行器循环测试"""
    
    @pytest.mark.asyncio
    async def test_executor_loop_count(self, mock_page, mock_browser):
        """测试执行器循环次数"""
        steps = [
            {
                "action_id": "loop",
                "params": {
                    "loop_count": 3,
                    "children": [
                        {"action_id": "click", "params": {"selector": "#item"}}
                    ]
                }
            }
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            mid="123"
        )
        
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].data["iterations"] == 3
    
    @pytest.mark.asyncio
    async def test_executor_loop_with_condition(self, mock_page, mock_browser):
        """测试带条件的循环"""
        variables = {"counter": 0}
        
        steps = [
            {
                "action_id": "loop",
                "params": {
                    "loop_while": "state.get('counter', 0) < 3",
                    "children": [
                        {"action_id": "click", "params": {"selector": "#btn"}}
                    ]
                }
            }
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            variables=variables,
            mid="123"
        )
        
        assert len(results) == 1
        assert results[0].success is True


class TestActionExecutorCondition:
    """执行器条件测试"""
    
    @pytest.mark.asyncio
    async def test_executor_if_true(self, mock_page, mock_browser):
        """测试条件为真"""
        steps = [
            {
                "action_id": "if_else",
                "params": {
                    "condition": "True",
                    "true_branch": [
                        {"action_id": "click", "params": {"selector": "#true-btn"}}
                    ],
                    "false_branch": [
                        {"action_id": "click", "params": {"selector": "#false-btn"}}
                    ]
                }
            }
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            mid="123"
        )
        
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].data["branch"] == "true_branch"
    
    @pytest.mark.asyncio
    async def test_executor_if_false(self, mock_page, mock_browser):
        """测试条件为假"""
        steps = [
            {
                "action_id": "if_else",
                "params": {
                    "condition": "False",
                    "true_branch": [
                        {"action_id": "click", "params": {"selector": "#true-btn"}}
                    ],
                    "false_branch": [
                        {"action_id": "click", "params": {"selector": "#false-btn"}}
                    ]
                }
            }
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            mid="123"
        )
        
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].data["branch"] == "false_branch"


class TestActionExecutorErrorHandling:
    """执行器错误处理测试"""
    
    @pytest.mark.asyncio
    async def test_executor_continue_on_error(self, mock_page, mock_browser):
        """测试错误时继续执行"""
        steps = [
            {"action_id": "click", "params": {"selector": "#exists"}},
            {"action_id": "click", "params": {"selector": "#not-exists", "on_error": "continue"}},
            {"action_id": "input", "params": {"selector": "#input", "value": "test"}}
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            mid="123"
        )
        
        # 即使中间步骤失败，也应该继续执行
        assert len(results) == 3
    
    @pytest.mark.asyncio
    async def test_executor_stop_on_error(self, mock_page, mock_browser):
        """测试错误时停止"""
        steps = [
            {"action_id": "click", "params": {"selector": "#exists"}},
            {"action_id": "click", "params": {"selector": "#not-exists", "on_error": "stop"}},
            {"action_id": "input", "params": {"selector": "#input", "value": "test"}}  # 不会执行
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            mid="123"
        )
        
        # 错误时停止，只执行了前两个
        assert len(results) <= 2


class TestActionExecutorTemplate:
    """执行器模板变量测试"""
    
    @pytest.mark.asyncio
    async def test_template_simple(self, mock_page, mock_browser):
        """测试简单模板替换"""
        variables = {"name": "John"}
        
        steps = [
            {"action_id": "input", "params": {"selector": "#name", "value": "{{name}}"}}
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            variables=variables,
            mid="123"
        )
        
        assert results[0].success is True
    
    @pytest.mark.asyncio
    async def test_template_nested(self, mock_page, mock_browser):
        """测试嵌套模板替换"""
        variables = {"user": {"name": "John", "age": 30}}
        
        steps = [
            {"action_id": "input", "params": {"selector": "#name", "value": "{{user.name}}"}},
            {"action_id": "input", "params": {"selector": "#age", "value": "{{user.age}}"}}
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            variables=variables,
            mid="123"
        )
        
        assert len(results) == 2
        assert all(r.success for r in results)


class TestActionExecutorIntegration:
    """执行器集成测试"""
    
    @pytest.mark.asyncio
    async def test_full_workflow(self, mock_page, mock_browser):
        """测试完整工作流"""
        variables = {"url": "https://example.com", "username": "test"}
        
        steps = [
            {"action_id": "navigate", "params": {"url": "{{url}}"}},
            {"action_id": "click", "params": {"selector": "#login-btn"}},
            {"action_id": "input", "params": {"selector": "#username", "value": "{{username}}"}},
            {
                "action_id": "if_else",
                "params": {
                    "condition": "True",
                    "true_branch": [
                        {"action_id": "click", "params": {"selector": "#submit"}}
                    ],
                    "false_branch": []
                }
            },
            {"action_id": "screenshot"}
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            variables=variables,
            mid="123"
        )
        
        assert len(results) == 5
        assert all(r.success for r in results)
    
    @pytest.mark.asyncio
    async def test_complex_nested_structure(self, mock_page, mock_browser):
        """测试复杂嵌套结构"""
        steps = [
            {"action_id": "navigate", "params": {"url": "https://example.com"}},
            {
                "action_id": "loop",
                "params": {
                    "loop_count": 2,
                    "children": [
                        {"action_id": "click", "params": {"selector": "#item"}},
                        {
                            "action_id": "if_else",
                            "params": {
                                "condition": "True",
                                "true_branch": [
                                    {"action_id": "input", "params": {"selector": "#field", "value": "test"}}
                                ],
                                "false_branch": []
                            }
                        }
                    ]
                }
            }
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            mid="123"
        )
        
        assert len(results) == 2  # navigate + loop
        assert results[0].success is True
        assert results[1].success is True


class TestActionExecutorOutput:
    """执行器输出测试"""
    
    @pytest.mark.asyncio
    async def test_output_collection(self, mock_page, mock_browser):
        """测试输出收集"""
        ctx = ActionContext(
            session_id="test",
            browser_id="test",
            page=mock_page,
            browser=mock_browser,
            variables={}
        )
        
        steps = [
            {"action_id": "click", "params": {"selector": "#btn"}},
            {"action_id": "input", "params": {"selector": "#input", "value": "test"}}
        ]
        
        results = await action_executor.execute_steps(
            steps=steps,
            page=mock_page,
            browser=mock_browser,
            variables=ctx.variables,
            mid="123"
        )
        
        # 验证结果被收集到 ctx
        assert len(results) == 2
        assert "result_0" in ctx.variables or "result_1" in ctx.variables
