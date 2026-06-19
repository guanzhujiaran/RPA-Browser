"""
测试控制流操作
"""
import pytest
from playwright.async_api import Page

from app.models.execution.action_params import LoopParams, IfElseParams, create_workflow_step
from app.models.execution.condition_models import (
    ConditionRule,
    ParamsCondition,
    ConditionValueType,
    LogicOperator,
)


class TestLoopAction:
    """循环操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_loop_with_count_and_click(self):
        """测试按次数循环 - 真实执行循环内的点击操作"""
        from app.services.execution.actions.control_flow import LoopAction

        await self.page.set_content(
            "<html><body>"
            "<button class='item' data-index='0'>Item 0</button>"
            "<button class='item' data-index='1'>Item 1</button>"
            "<button class='item' data-index='2'>Item 2</button>"
            "</body></html>"
        )

        action = LoopAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=LoopParams(
                count=3,
                loopBranch=[
                    create_workflow_step(action_id="click", params={"selector": ".item:nth-child({{state.loop.index}})"}),
                ],
            ),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_loop_with_items(self):
        """测试按列表循环"""
        from app.services.execution.actions.control_flow import LoopAction

        await self.page.set_content(
            "<html><body>"
            "<input id='input_a' value=''>"
            "<input id='input_b' value=''>"
            "</body></html>"
        )

        action = LoopAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=LoopParams(
                items=["a", "b"],
                loopBranch=[
                    create_workflow_step(action_id="input", params={"selector": "#input_{{state.loop.current_item}}", "value": "value_{{state.loop.current_item}}"}),
                ],
            ),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_loop_missing_params(self):
        """测试缺少循环参数"""
        from app.services.execution.actions.control_flow import LoopAction

        action = LoopAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=LoopParams(),
        )

        result = await action.execute()
        assert result.success
        assert "无子步骤可执行" in (result.data.message or "")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_loop_with_screenshot(self):
        """测试循环内包含截图操作"""
        from app.services.execution.actions.control_flow import LoopAction

        await self.page.set_content(
            "<html><body>"
            "<div id='slide1' style='width: 100px; height: 50px; background: red;'>Slide 1</div>"
            "<div id='slide2' style='width: 100px; height: 50px; background: blue;'>Slide 2</div>"
            "</body></html>"
        )

        action = LoopAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=LoopParams(
                count=2,
                loopBranch=[
                    create_workflow_step(action_id="screenshot", params={}),
                ],
            ),
        )

        result = await action.execute()
        assert result.success


class TestIfElseAction:
    """条件分支操作测试"""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        self.page = page

    @pytest.mark.asyncio(loop_scope="session")
    async def test_if_else_with_true_condition(self):
        """测试条件为真的分支 - 执行 true 分支内的操作"""
        from app.services.execution.actions.control_flow import IfElseAction

        await self.page.set_content(
            "<html><body>"
            "<button id='true_btn'>True</button>"
            "</body></html>"
        )

        action = IfElseAction.new_action(
            mid=1,
            page=self.page,
            variables={"should_click": True},
            params=IfElseParams(
                condition=ConditionRule(
                    logic=LogicOperator.AND,
                    condition=ParamsCondition(
                        field="should_click",
                        condition_value_type=ConditionValueType.BOOLEAN,
                        condition_value=True,
                    ),
                ),
                TrueBranch=[
                    create_workflow_step(action_id="click", params={"selector": "#true_btn"}),
                ],
                FalseBranch=[],
            ),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_if_else_with_false_condition(self):
        """测试条件为假的分支 - 执行 false 分支内的操作"""
        from app.services.execution.actions.control_flow import IfElseAction

        await self.page.set_content(
            "<html><body>"
            "<button id='false_btn'>False</button>"
            "</body></html>"
        )

        action = IfElseAction.new_action(
            mid=1,
            page=self.page,
            variables={"should_click": False},
            params=IfElseParams(
                condition=ConditionRule(
                    logic=LogicOperator.AND,
                    condition=ParamsCondition(
                        field="should_click",
                        condition_value_type=ConditionValueType.BOOLEAN,
                        condition_value=True,
                    ),
                ),
                TrueBranch=[],
                FalseBranch=[
                    create_workflow_step(action_id="click", params={"selector": "#false_btn"}),
                ],
            ),
        )

        result = await action.execute()
        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_if_else_missing_condition(self):
        """测试条件变量不存在时的回退行为"""
        from app.services.execution.actions.control_flow import IfElseAction

        action = IfElseAction.new_action(
            mid=1,
            page=self.page,
            variables={"test": True},
            params=IfElseParams(
                condition=ConditionRule(
                    logic=LogicOperator.AND,
                    condition=ParamsCondition(
                        field="nonexistent_var",
                        condition_value_type=ConditionValueType.BOOLEAN,
                        condition_value=True,
                    ),
                ),
            ),
        )

        result = await action.execute()
        assert result.success
        # 变量不存在会导致条件评估失败，走 false 分支（空分支），结果是成功但没有执行任何操作

    @pytest.mark.asyncio(loop_scope="session")
    async def test_if_else_with_screenshot_branch(self):
        """测试条件分支包含截图"""
        from app.services.execution.actions.control_flow import IfElseAction

        await self.page.set_content(
            "<html><body>"
            "<div id='content'>Content</div>"
            "</body></html>"
        )

        action = IfElseAction.new_action(
            mid=1,
            page=self.page,
            variables={"take_screenshot": True},
            params=IfElseParams(
                condition=ConditionRule(
                    logic=LogicOperator.AND,
                    condition=ParamsCondition(
                        field="take_screenshot",
                        condition_value_type=ConditionValueType.BOOLEAN,
                        condition_value=True,
                    ),
                ),
                TrueBranch=[
                    create_workflow_step(action_id="screenshot", params={}),
                ],
                FalseBranch=[],
            ),
        )

        result = await action.execute()
        assert result.success