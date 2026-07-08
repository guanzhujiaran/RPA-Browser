"""
测试 LLM Action（基于 LangChain ChatOpenAI，支持 structured output）
使用 unittest.mock 模拟 ChatOpenAI.ainvoke / with_structured_output
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.execution.actions.llm import (
    LLMAction,
    _create_model_from_schema,
)
from app.models.execution.action_params import LLMParams


# ── 辅助函数 ───────────────────────────────────────────────

def _make_text_aimessage(content: str, model: str = "gpt-3.5-turbo",
                         usage: dict | None = None):
    from langchain_core.messages import AIMessage
    msg = AIMessage(content=content)
    msg.response_metadata = {"model_name": model}
    if usage:
        msg.response_metadata["token_usage"] = usage
        from types import SimpleNamespace
        msg.usage_metadata = SimpleNamespace(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )
    return msg


def _make_structured_result(data: dict):
    """创建模拟的结构化输出结果"""
    mock = MagicMock()
    mock.model_dump.return_value = data
    mock.response_metadata = {}
    mock.usage_metadata = None
    return mock


def _make_llm_params(**overrides) -> LLMParams:
    defaults = {
        "server_url": "https://api.openai.com/v1",
        "api_key": "sk-test-key",
        "model": "gpt-3.5-turbo",
        "prompt": "你好",
        "system_prompt": "你是一个有用的助手",
        "temperature": 0.7,
        "max_tokens": 2048,
        "timeout": 60000,
    }
    defaults.update(overrides)
    return LLMParams(**defaults)


def _patch_chatopenai_text(mock_message):
    """patch ChatOpenAI.ainvoke 返回指定消息（文本模式）"""
    mock_instance = AsyncMock()
    if isinstance(mock_message, Exception):
        mock_instance.ainvoke.side_effect = mock_message
    else:
        mock_instance.ainvoke.return_value = mock_message
    return patch(
        "app.services.execution.actions.llm.ChatOpenAI",
        return_value=mock_instance,
    )


def _patch_chatopenai_structured(mock_result):
    """patch ChatOpenAI.with_structured_output → ainvoke 返回结构化结果"""
    mock_chat = AsyncMock()
    mock_structured = AsyncMock()
    if isinstance(mock_result, Exception):
        mock_structured.ainvoke.side_effect = mock_result
    else:
        mock_structured.ainvoke.return_value = mock_result
    mock_chat.with_structured_output.return_value = mock_structured
    return patch(
        "app.services.execution.actions.llm.ChatOpenAI",
        return_value=mock_chat,
    )


# ── 动态模型构建测试 ───────────────────────────────────────

class TestCreateModelFromSchema:
    def test_basic_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "用户名"},
                "age": {"type": "integer", "description": "年龄"},
            },
            "required": ["name", "age"],
        }
        model = _create_model_from_schema(schema, "TestModel")
        instance = model(name="小明", age=18)
        assert instance.model_dump() == {"name": "小明", "age": 18}

    def test_optional_fields(self):
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["title"],
        }
        model = _create_model_from_schema(schema)
        instance = model(title="Hello")
        assert instance.count is None

    def test_empty_properties_fallback(self):
        schema = {"type": "object", "properties": {}}
        model = _create_model_from_schema(schema)
        instance = model(content="fallback")
        assert instance.content == "fallback"

    def test_boolean_and_number(self):
        schema = {
            "type": "object",
            "properties": {
                "active": {"type": "boolean"},
                "score": {"type": "number"},
            },
            "required": ["active"],
        }
        model = _create_model_from_schema(schema)
        instance = model(active=True, score=95.5)
        assert instance.active is True
        assert instance.score == 95.5


# ── 文本模式测试 ───────────────────────────────────────────

class TestLLMActionText:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_basic_prompt(self):
        mock_msg = _make_text_aimessage(
            "你好！我是 AI 助手。",
            usage={"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
        )
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(prompt="你好"),
        )
        with _patch_chatopenai_text(mock_msg):
            result = await action.execute()
        assert result.success
        assert result.data.content == "你好！我是 AI 助手。"
        assert result.data.role == "assistant"
        assert result.data.model == "gpt-3.5-turbo"
        assert not result.data.is_structured
        assert result.data.structured_data is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_system_prompt(self):
        mock_msg = _make_text_aimessage("翻译结果: Hello", model="gpt-4")
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(prompt="翻译'你好'", system_prompt="你是一个翻译助手"),
        )
        with _patch_chatopenai_text(mock_msg):
            result = await action.execute()
        assert result.success
        assert result.data.content == "翻译结果: Hello"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_empty_response(self):
        mock_msg = _make_text_aimessage("")
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(prompt="hello"),
        )
        with _patch_chatopenai_text(mock_msg):
            result = await action.execute()
        assert result.success
        assert result.data.content == ""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_prompt(self):
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(prompt=""),
        )
        result = await action.execute()
        assert not result.success
        assert "prompt" in result.error.lower()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_invalid_params(self):
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=LLMParams(server_url="", api_key="", model=""),
        )
        result = await action.execute()
        assert not result.success


# ── 错误处理测试 ───────────────────────────────────────────

class TestLLMActionErrors:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_api_401_error(self):
        """401 认证错误直接返回失败"""
        error = Exception("Error code: 401 - Incorrect API key provided")
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(prompt="hello"),
        )
        with _patch_chatopenai_text(error):
            result = await action.execute()
        assert not result.success
        assert "401" in result.error

    @pytest.mark.asyncio(loop_scope="session")
    async def test_api_timeout(self):
        """超时直接返回失败"""
        error = Exception("Request timed out")
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(prompt="hello", timeout=1000),
        )
        with _patch_chatopenai_text(error):
            result = await action.execute()
        assert not result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_connection_error(self):
        """连接错误直接返回失败"""
        error = Exception("Connection refused")
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(prompt="hello"),
        )
        with _patch_chatopenai_text(error):
            result = await action.execute()
        assert not result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_max_tokens_limit(self):
        """token 超限错误"""
        error = Exception("Error code: 400 - This model's maximum context length is 4096 tokens")
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(
                prompt="用超长文本" + "A" * 10000, max_tokens=50,
            ),
        )
        with _patch_chatopenai_text(error):
            result = await action.execute()
        assert not result.success
        assert "4096" in result.error


# ── 结构化输出测试 ─────────────────────────────────────────

class TestLLMActionStructured:
    LOTTERY_SCHEMA = {
        "type": "object",
        "properties": {
            "prize_name": {"type": "string", "description": "奖品名称"},
            "is_lottery": {"type": "boolean", "description": "是否为抽奖动态"},
            "need_repost": {"type": "boolean", "description": "是否需要转发"},
            "lottery_time": {"type": "string", "description": "开奖时间"},
        },
        "required": ["prize_name", "is_lottery"],
    }

    @pytest.mark.asyncio(loop_scope="session")
    async def test_structured_output_success(self):
        mock_result = _make_structured_result({
            "prize_name": "iPhone 15",
            "is_lottery": True,
            "need_repost": True,
            "lottery_time": "2025-01-15 20:00:00",
        })
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(
                prompt="解析这条动态的抽奖信息",
                response_schema=self.LOTTERY_SCHEMA,
            ),
        )
        with _patch_chatopenai_structured(mock_result):
            result = await action.execute()
        assert result.success
        assert result.data.is_structured
        assert result.data.structured_data == {
            "prize_name": "iPhone 15",
            "is_lottery": True,
            "need_repost": True,
            "lottery_time": "2025-01-15 20:00:00",
        }
        assert "iPhone 15" in result.data.content

    @pytest.mark.asyncio(loop_scope="session")
    async def test_structured_output_minimal(self):
        """仅必填字段有值"""
        mock_result = _make_structured_result({
            "prize_name": "红包",
            "is_lottery": False,
            "need_repost": None,
            "lottery_time": None,
        })
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(
                prompt="解析抽奖信息",
                response_schema=self.LOTTERY_SCHEMA,
            ),
        )
        with _patch_chatopenai_structured(mock_result):
            result = await action.execute()
        assert result.success
        assert result.data.structured_data["prize_name"] == "红包"
        assert result.data.structured_data["is_lottery"] is False

    @pytest.mark.asyncio(loop_scope="session")
    async def test_structured_parse_error(self):
        """结构化解析失败直接返回错误"""
        error = Exception("Failed to parse JSON from model output")
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(
                prompt="解析抽奖信息",
                response_schema=self.LOTTERY_SCHEMA,
            ),
        )
        with _patch_chatopenai_structured(error):
            result = await action.execute()
        assert not result.success


# ── 变量替换测试 ───────────────────────────────────────────

class TestLLMActionVariables:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_variables(self):
        mock_msg = _make_text_aimessage("你叫小明")
        action = LLMAction.new_action(
            mid=1, page=None,
            variables={"name": "小明"},
            params=_make_llm_params(prompt="我的名字是什么", system_prompt="我的名字是{name}"),
        )
        with _patch_chatopenai_text(mock_msg):
            result = await action.execute()
        assert result.success
        assert result.data.content == "你叫小明"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_output_variables(self):
        mock_msg = _make_text_aimessage("结果文本")
        action = LLMAction.new_action(
            mid=1, page=None, variables={},
            params=_make_llm_params(prompt="hello"),
            output_vars=["llm_result"],
        )
        with _patch_chatopenai_text(mock_msg):
            result = await action.execute()
        assert result.success
        assert result.variables.get("llm_result") == "结果文本"
