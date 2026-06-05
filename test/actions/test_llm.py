"""
测试 LLM Action
使用 unittest.mock 模拟 httpx 请求
LLM Action 不需要浏览器，只使用 mock
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.services.execution.actions.llm import LLMAction
from app.models.execution.action_params import LLMParams


class TestLLMAction:
    """LLM 对话操作测试"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_with_prompt(self):
        """测试使用 prompt 单轮对话"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "choices": [{"message": {"content": "你好！我是 AI 助手。", "role": "assistant"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
        })
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

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

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await action.execute()

        assert result.success
        assert result.data["content"] == "你好！我是 AI 助手。"
        assert result.data["role"] == "assistant"
        assert result.data["model"] == "gpt-3.5-turbo"
        assert result.data["text"] == "你好！我是 AI 助手。"
        assert result.data["answer"] == "你好！我是 AI 助手。"
        assert "usage" in result.data

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_with_messages(self):
        """测试使用 messages 多轮对话"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "choices": [{"message": {"content": "根据历史，答案是 42。", "role": "assistant"}}],
            "usage": {"total_tokens": 50},
        })
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

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

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await action.execute()

        assert result.success
        assert "42" in result.data["content"]

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
        """测试 API 返回错误状态码"""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.json = MagicMock(side_effect=ValueError("No JSON"))

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

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

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await action.execute()

        assert not result.success
        assert "401" in result.error

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_api_timeout(self):
        """测试请求超时"""
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.side_effect = __import__("httpx").TimeoutException("Timeout")

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

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await action.execute()

        assert not result.success
        assert "超时" in result.error

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_empty_response(self):
        """测试 API 返回空 choices"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "choices": [],
            "usage": {},
        })
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

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

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await action.execute()

        assert not result.success
        assert "未找到 choices" in result.error

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_with_system_prompt(self):
        """测试系统提示词"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "choices": [{"message": {"content": "翻译结果: Hello", "role": "assistant"}}],
            "usage": {},
        })
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

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

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await action.execute()

        assert result.success
        assert result.data["content"] == "翻译结果: Hello"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_connection_error(self):
        """测试连接错误"""
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.side_effect = __import__("httpx").RequestError("Connection refused")

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

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await action.execute()

        assert not result.success
        assert "请求失败" in result.error

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
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "choices": [{"message": {"content": "你叫小明", "role": "assistant"}}],
            "usage": {},
        })
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

        action = LLMAction.new_action(
            mid=1,
            page=None,
            variables={"name": "小明", "test": True},
            params=LLMParams(
                server_url="https://api.openai.com/v1",
                api_key="sk-test-key",
                model="gpt-3.5-turbo",
                prompt="我的名字是什么",
                system_prompt="你的名字是{name}",
                timeout=60000,
            ),
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await action.execute()

        assert result.success

    @pytest.mark.asyncio(loop_scope="session")
    async def test_llm_max_tokens_limit(self):
        """测试 max_tokens 限制 - 应报错中断后续操作"""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json = MagicMock(return_value={
            "error": {"message": "This model's maximum context length is 4096 tokens"}
        })
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

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

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await action.execute()

        assert not result.success
        assert "400" in result.error