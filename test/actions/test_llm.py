"""
测试 LLM Action（基于 LangChain ChatOpenAI）
使用 unittest.mock 模拟 ChatOpenAI.ainvoke
"""
import pytest
from unittest.mock import patch, AsyncMock
from langchain_core.messages import AIMessage

from app.services.execution.actions.llm import LLMAction
from app.models.execution.action_params import LLMParams


def _make_ai_message(content: str, model: str = "gpt-3.5-turbo",
                     usage: dict | None = None) -> AIMessage:
    """创建带 metadata 的模拟 AIMessage"""
    msg = AIMessage(content=content)
    msg.response_metadata = {"model_name": model}
    if usage:
        msg.response_metadata["token_usage"] = usage
        # 兼容 usage_metadata
        from types import SimpleNamespace
        msg.usage_metadata = SimpleNamespace(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )
    return msg


def _patch_chatopenai(mock_message: AIMessage | Exception):
    """返回一个 patch 上下文，将 ChatOpenAI.ainvoke mock 为返回指定消息或抛出异常"""
    mock_instance = AsyncMock()
    if isinstance(mock_message, Exception):
        mock_instance.ainvoke.side_effect = mock_message
    else:
        mock_instance.ainvoke.return_value = mock_message
    return patch("app.services.execution.actions.llm.ChatOpenAI", return_value=mock_instance)


class TestLLMAction:
    """LLM 对话操作测试（LangChain 版本）"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_with_prompt(self):
        """测试使用 prompt 单轮对话"""
        mock_msg = _make_ai_message(
            "你好！我是 AI 助手。",
            usage={"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
        )
        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="sk-test-key",
                model="gpt-3.5-turbo",
                prompt="你好",
                system_prompt="你是一个有用的助手",
                temperature=0.7,
                max_tokens=2048,
                timeout=60000,
            ),
        )

        with _patch_chatopenai(mock_msg):
            result = await action.execute()

        assert result.success
        assert result.data.content == "你好！我是 AI 助手。"
        assert result.data.role == "assistant"
        assert result.data.model == "gpt-3.5-turbo"
        assert "usage" in result.data.model_dump()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_with_messages(self):
        """测试使用 messages 多轮对话"""
        mock_msg = _make_ai_message("根据历史，答案是 42。", model="gpt-4")
        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="sk-test-key",
                model="gpt-4",
                messages=[
                    {"role": "user", "content": "1+1=?"},
                    {"role": "assistant", "content": "2"},
                ],
                prompt="前面我问了什么？",
                timeout=60000,
            ),
        )

        with _patch_chatopenai(mock_msg):
            result = await action.execute()

        assert result.success
        assert "42" in result.data.content

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_missing_messages_and_prompt(self):
        """测试 messages 和 prompt 同时为空"""
        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="sk-test-key",
                model="gpt-3.5-turbo",
                messages=[],
                prompt="",
            ),
        )

        result = await action.execute()
        assert not result.success
        assert "messages 或 prompt 不能同时为空" in result.error

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_api_error(self):
        """测试 API 返回错误（401）"""
        error = Exception("Error code: 401 - Incorrect API key provided")

        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="invalid-key",
                model="gpt-3.5-turbo",
                prompt="hello",
                timeout=60000,
            ),
        )

        with _patch_chatopenai(error):
            result = await action.execute()

        assert not result.success
        assert "401" in result.error

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_api_timeout(self):
        """测试请求超时"""
        error = Exception("Request timed out")

        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="sk-test-key",
                model="gpt-3.5-turbo",
                prompt="hello",
                timeout=1000,
            ),
        )

        with _patch_chatopenai(error):
            result = await action.execute()

        assert not result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_empty_response(self):
        """测试 API 返回空内容"""
        mock_msg = _make_ai_message("")

        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="sk-test-key",
                model="gpt-3.5-turbo",
                prompt="hello",
                timeout=60000,
            ),
        )

        with _patch_chatopenai(mock_msg):
            result = await action.execute()

        assert result.success
        assert result.data.content == ""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_with_system_prompt(self):
        """测试系统提示词"""
        mock_msg = _make_ai_message("翻译结果: Hello", model="gpt-4")
        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="sk-test-key",
                model="gpt-4",
                prompt="翻译中文'你好'到英文",
                system_prompt="你是一个翻译助手，只输出翻译结果",
                temperature=0.3,
                max_tokens=500,
                timeout=60000,
            ),
        )

        with _patch_chatopenai(mock_msg):
            result = await action.execute()

        assert result.success
        assert result.data.content == "翻译结果: Hello"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_connection_error(self):
        """测试连接错误"""
        error = Exception("Connection refused")

        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="https://invalid-server.example.com",
                api_key="sk-test-key",
                model="gpt-3.5-turbo",
                prompt="hello",
                timeout=60000,
            ),
        )

        with _patch_chatopenai(error):
            result = await action.execute()

        assert not result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_invalid_params(self):
        """测试缺少必填参数"""
        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="",
                api_key="",
                model="",
            ),
        )
        result = await action.execute()
        assert not result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_with_input_variables(self):
        """测试使用输入变量"""
        mock_msg = _make_ai_message("你叫小明")
        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"name": "小明", "test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="sk-test-key",
                model="gpt-3.5-turbo",
                prompt="我的名字是什么",
                system_prompt="我的名字是{name}",
                timeout=60000,
            ),
        )

        with _patch_chatopenai(mock_msg):
            result = await action.execute()

        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_max_tokens_limit(self):
        """测试 max_tokens 限制（400 错误）"""
        error = Exception("Error code: 400 - This model's maximum context length is 4096 tokens")

        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="sk-test-key",
                model="gpt-3.5-turbo",
                prompt="用超长文本" + "A" * 10000,
                max_tokens=50,
                timeout=60000,
            ),
        )

        with _patch_chatopenai(error):
            result = await action.execute()

        assert not result.success
        assert "4096" in result.error
