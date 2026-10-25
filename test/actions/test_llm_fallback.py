"""LLM 模型回退单测（离线，不发起真实请求）

对应 ``docs/LLM模型回退计划书.md``：
- 主模型正常 → 不触发备用模型；
- 主模型抛异常 → 按顺序回退到备用模型，结果标记 fallback_used；
- 全部失败 → 动作失败，错误为最后一个模型的异常；
- 未配置备用模型 → 行为与改造前一致。
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.models.execution.action_params import LLMModelConfig, LLMParams
from app.services.execution.actions.llm import LLMAction


def make_params(fallbacks: list[tuple[str, Exception | None]]) -> LLMParams:
    return LLMParams(
        model="primary-model",
        server_url="https://primary/api",
        api_key="k1",
        system_prompt="sys",
        prompt="写一条短评",
        fallbacks=[
            LLMModelConfig(model=name, server_url=f"https://{name}/api", api_key="k2")
            for name, _ in fallbacks
        ],
    )


@pytest.fixture
def fake_models(monkeypatch):
    """把 _build_chat_model 换成可控的假模型（RunnableLambda）"""
    calls: list[str] = []
    behaviors: dict[str, Exception | None] = {}

    def fake_build(self, *, model, server_url, api_key, params):
        async def _fn(messages):
            calls.append(model)
            err = behaviors.get(model)
            if err is not None:
                raise err
            return AIMessage(
                content=f"来自{model}的评论", response_metadata={"model_name": model}
            )

        return RunnableLambda(_fn)

    monkeypatch.setattr(LLMAction, "_build_chat_model", fake_build)
    return MagicMock(calls=calls, behaviors=behaviors)


async def run_action(params: LLMParams):
    action = LLMAction.new_action(
        mid=1, page=None, variables={}, params=params
    )
    return await action.execute()


@pytest.mark.asyncio
async def test_primary_success_no_fallback(fake_models):
    fake_models.behaviors.update({"primary-model": None, "backup-1": None})
    params = make_params([("backup-1", None)])

    result = await run_action(params)

    assert result.success
    assert result.data.content == "来自primary-model的评论"
    assert result.data.fallback_used is False
    assert fake_models.calls == ["primary-model"]


@pytest.mark.asyncio
async def test_falls_back_on_rate_limit(fake_models):
    fake_models.behaviors.update(
        {"primary-model": RuntimeError("Error code: 429 free-models-per-day")}
    )
    params = make_params([("backup-1", None)])

    result = await run_action(params)

    assert result.success, "主模型失败应回退到备用模型"
    assert result.data.content == "来自backup-1的评论"
    assert result.data.model == "backup-1"
    assert result.data.fallback_used is True
    assert fake_models.calls == ["primary-model", "backup-1"]


@pytest.mark.asyncio
async def test_all_models_failed_returns_last_error(fake_models):
    fake_models.behaviors.update(
        {
            "primary-model": RuntimeError("Error code: 429 primary"),
            "backup-1": RuntimeError("Error code: 401 invalid api key backup-1"),
        }
    )
    params = make_params([("backup-1", None)])

    result = await run_action(params)

    assert result.success is False
    assert "backup-1" in (result.error or ""), "应上报最后一个模型的错误"
    assert fake_models.calls == ["primary-model", "backup-1"]


@pytest.mark.asyncio
async def test_without_fallbacks_behaves_as_before(fake_models):
    fake_models.behaviors.update({"primary-model": RuntimeError("boom")})
    params = make_params([])

    result = await run_action(params)

    assert result.success is False
    assert "boom" in (result.error or "")
    assert fake_models.calls == ["primary-model"]


class TestFallbacksParamCoercion:
    """`fallbacks` 既可以是数组，也可以是工作流变量替换后的文本"""

    BASE = {"server_url": "https://api/x", "api_key": "k", "model": "m"}

    def test_json_string(self):
        params = LLMParams(
            **self.BASE,
            fallbacks='[{"model":"b","server_url":"https://b","api_key":"kb"}]',
        )
        assert [fb.model for fb in params.fallbacks] == ["b"]

    def test_python_repr_from_template(self):
        """模板替换列表变量后是 Python repr（单引号），也要能解析"""
        params = LLMParams(
            **self.BASE,
            fallbacks="[{'model': 'b', 'server_url': 'https://b', 'api_key': 'kb'}]",
        )
        assert [fb.model for fb in params.fallbacks] == ["b"]

    def test_empty_string_means_no_fallback(self):
        assert LLMParams(**self.BASE, fallbacks="").fallbacks == []
        assert LLMParams(**self.BASE, fallbacks="[]").fallbacks == []
        assert LLMParams(**self.BASE).fallbacks == []

    def test_invalid_text_degrades_to_empty(self):
        """配置写错不能让步骤解析失败（否则详情/执行链路整条挂掉），降级为单模型"""
        assert LLMParams(**self.BASE, fallbacks="not-a-list").fallbacks == []

    def test_template_string_degrades_to_empty(self):
        """`fallbacks` 里写模板串时先保持空，实际池子交给 fallbacks_json"""
        assert LLMParams(**self.BASE, fallbacks="{{llm_fallbacks}}").fallbacks == []

    def test_fallbacks_json_is_kept_for_runtime(self):
        params = LLMParams(**self.BASE, fallbacks_json="{{llm_fallbacks}}")
        assert params.fallbacks_json == "{{llm_fallbacks}}"
        assert params.fallbacks == []


class TestResolveFallbacksFromJson:
    """运行时：fallbacks_json（模板替换后的文本）→ 备用模型配置"""

    def test_parse_json_text(self):
        params = LLMParams(
            server_url="u",
            api_key="k",
            model="m",
            fallbacks_json='[{"model":"b","server_url":"https://b","api_key":"kb"}]',
        )
        models = LLMAction._resolve_fallbacks(params)
        assert [m.model for m in models] == ["b"]

    def test_parse_python_repr(self):
        params = LLMParams(
            server_url="u",
            api_key="k",
            model="m",
            fallbacks_json="[{'model': 'b', 'server_url': 'https://b', 'api_key': 'kb'}]",
        )
        assert [m.model for m in LLMAction._resolve_fallbacks(params)] == ["b"]

    def test_invalid_json_degrades(self):
        params = LLMParams(
            server_url="u", api_key="k", model="m", fallbacks_json="{{not_resolved}}"
        )
        assert LLMAction._resolve_fallbacks(params) == []

    def test_fallbacks_list_takes_priority(self):
        params = LLMParams(
            server_url="u",
            api_key="k",
            model="m",
            fallbacks=[LLMModelConfig(model="direct", server_url="u2", api_key="k2")],
            fallbacks_json='[{"model":"ignored","server_url":"u3","api_key":"k3"}]',
        )
        assert [m.model for m in LLMAction._resolve_fallbacks(params)] == ["direct"]
