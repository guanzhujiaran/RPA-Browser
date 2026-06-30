"""
测试打印参数操作（调试用）
PrintAction 不依赖浏览器，仅打印变量替换后的内容，不执行实际操作
"""
import pytest

from app.models.execution.action_params import PrintParams


class TestPrintAction:
    """打印参数操作测试"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_print_message(self):
        """测试打印消息内容"""
        from app.services.execution.actions.debug import PrintAction

        action = PrintAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=PrintParams(message="hello world"),
        )

        result = await action.execute()
        assert result.success
        assert result.data.message == "hello world"
        assert result.error is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_print_empty_message(self):
        """测试空消息（默认值）"""
        from app.services.execution.actions.debug import PrintAction

        action = PrintAction.new_action(
            mid=1,
            page=None,
            variables={},
            params=PrintParams(),
        )

        result = await action.execute()
        assert result.success
        assert result.data.message == ""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_print_without_params(self):
        """测试无参数（params=None，使用默认值）"""
        from app.services.execution.actions.debug import PrintAction

        action = PrintAction.new_action(
            mid=1,
            page=None,
            variables={},
        )

        result = await action.execute()
        assert result.success
        assert result.data.message == ""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_print_with_output_vars(self):
        """测试 output_vars 合并：message 应被赋值到指定变量"""
        from app.services.execution.actions.debug import PrintAction

        action = PrintAction.new_action(
            mid=1,
            page=None,
            variables={},
            params=PrintParams(message="debug content"),
            output_vars=["printed_msg"],
        )

        result = await action.execute()
        assert result.success
        # output_vars 按顺序取 data 的值
        assert result.variables.get("printed_msg") == "debug content"
        # last_output 始终是完整的 data
        assert result.variables.get("last_output") is not None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_print_does_not_require_browser(self):
        """测试 PrintAction 不需要浏览器上下文（page=None 也能正常执行）"""
        from app.services.execution.actions.debug import PrintAction

        action = PrintAction.new_action(
            mid=1,
            page=None,
            variables={},
            params=PrintParams(message="no browser needed"),
        )

        result = await action.execute()
        assert result.success
        assert result.data.message == "no browser needed"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_print_metadata(self):
        """测试 PrintAction 元数据注册正确"""
        from app.models.execution.action_params import (
            BuiltinActionType, BUILTIN_ACTION_PARAMS_MAP, BUILTIN_ACTION_RESULT_MAP,
        )
        from app.services.execution.actions.all_actions import BUILTIN_ACTION_MAP
        from app.services.execution.actions.debug import PrintAction

        # action_type 枚举
        assert BuiltinActionType.PRINT.value == "print"
        assert BuiltinActionType.PRINT.nameDisplay == "打印参数"

        # map 注册
        assert BUILTIN_ACTION_MAP.get("print") is PrintAction
        assert BUILTIN_ACTION_PARAMS_MAP.get("print") is PrintParams
        assert BuiltinActionType.PRINT.result_model.__name__ == "PrintResult"
        assert BuiltinActionType.PRINT.params_model is PrintParams

        # json_schema 含 message 字段
        schema = BuiltinActionType.PRINT.metadata.json_schema
        assert "message" in schema.get("properties", {})
