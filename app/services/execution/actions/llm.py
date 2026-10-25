"""
LLM Action — 基于 LangChain ChatOpenAI，支持结构化输出
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, create_model, Field as PydanticField

from app.services.execution.actions.base import ActionResult, BaseAction, register_action_class
from loguru import logger
from app.models.execution.action_params import (
    LLMModelConfig,
    LLMParams,
    LLMResult,
)
from app.models.execution.action_param_models import _parse_model_configs
from app.models.database.workflow.models import BuiltinActionType


# ── 动态 Pydantic 模型构建 ─────────────────────────────────

_JSON_TYPE_MAP: Dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _create_model_from_schema(
    schema: dict, model_name: str = "StructuredOutput"
) -> type[BaseModel]:
    """从 JSON Schema 动态创建 Pydantic 模型，用于 LangChain structured output。"""
    fields: Dict[str, Any] = {}
    properties = schema.get("properties", {})
    required: set = set(schema.get("required", []))

    for field_name, field_schema in properties.items():
        json_type = field_schema.get("type", "string")
        field_desc = field_schema.get("description", "")

        py_type = _JSON_TYPE_MAP.get(json_type)
        if py_type is None:
            py_type = str

        if field_name in required:
            fields[field_name] = (py_type, PydanticField(description=field_desc))
        else:
            fields[field_name] = (
                py_type | None,
                PydanticField(default=None, description=field_desc),
            )

    if not fields:
        fields["content"] = (str, PydanticField(description="提取的内容"))

    return create_model(model_name, **fields)


# ── LLMAction ──────────────────────────────────────────────


class LLMAction(BaseAction[LLMParams]):
    """LLM 对话操作（基于 LangChain ChatOpenAI，支持 structured output）"""

    action_id: BuiltinActionType = BuiltinActionType.LLM
    action_type: BuiltinActionType = BuiltinActionType.LLM
    params: LLMParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: LLMParams | None = None,
        timeout: int = 30000,
        input_vars: Dict | None = None,
        output_vars: List[str] | None = None,
        action_name: str | None = None,
    ):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            "action_id": cls.action_id,
            "action_type": cls.action_type,
            "mid": mid,
            "page": page,
            "params": safe_params,
            "timeout": timeout,
            "input_vars": input_vars or {},
            "output_vars": output_vars or [],
            "variables": variables or {},
        }
        if action_name is not None:
            kwargs["_action_name"] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[LLMResult]:
        start_time = time.time()

        # 参数校验
        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or not validated_params:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        prompt = validated_params.prompt
        if not prompt:
            return ActionResult(
                success=False,
                error="prompt 不能为空",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        messages = [
            SystemMessage(content=validated_params.system_prompt),
            HumanMessage(content=prompt),
        ]

        # 回退链上下文：先声明再进 try，保证任何分支（含超时）都能安全引用
        failures: list[tuple[str, str]] = []
        fallbacks: list[Any] = []
        budget_seconds = 0.0
        try:
            # 主模型 + 备用模型 → LangChain 回退链（主模型抛异常时按顺序切下一个）
            runnable = self._build_runnable(validated_params, failures)
            fallbacks = self._resolve_fallbacks(validated_params)
            # 调用前先落一条日志：LLM 是整条工作流里最可能长时间阻塞的一步
            # （每模型各自 timeout，回退链成倍放大），不打印就会出现
            # 「循环卡住、后续步骤全不执行且没有任何报错」的观感。
            logger.info(
                f"[LLMAction] 开始调用 LLM: 主模型={validated_params.model}, "
                f"备用模型={len(fallbacks)} 个, "
                f"单模型超时={validated_params.timeout}ms, "
                f"结构化输出={bool(validated_params.response_schema)}"
            )

            # 整条回退链必须有**硬上界**：底层 HTTP 客户端的 timeout 只在单次请求内
            # 生效，一旦某次请求卡在读响应体上（TLS 读挂起 / 服务端只回头不回体），
            # 这一步既不会失败也不会返回，工作流就停在原地：后面的输入框填写、点发送、
            # 点赞、转发全都不执行，而且没有任何错误可报（因为「还没失败」）。
            # 预算 = 单模型 timeout × 模型个数，超时即当作失败，交由 on_error 策略降级。
            budget_seconds = validated_params.timeout / 1000.0 * (1 + len(fallbacks))
            if validated_params.response_schema:
                call = self._call_structured(runnable, messages, validated_params)
            else:
                call = self._call_text(runnable, messages, validated_params)
            result = await asyncio.wait_for(call, timeout=budget_seconds)

            return ActionResult(
                success=True,
                data=result,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except asyncio.TimeoutError:
            error = (
                f"LLM 调用整体超时：预算 {budget_seconds:.0f}s "
                f"（单模型 {validated_params.timeout / 1000.0:.0f}s × "
                f"{1 + len(fallbacks)} 个模型），已强制终止"
            )
            logger.error(f"[LLMAction] {error}")
            if failures:
                detail = "; ".join(f"{name}: {msg[:300]}" for name, msg in failures)
                error = f"{error}；已失败模型 [{detail[:1200]}]"
            return ActionResult(
                success=False,
                error=error,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            # `with_fallbacks` 全失败时只抛出**第一个**异常，备用模型的失败原因会丢，
            # 因此用包装里收集到的 failures 拼一条完整说明（每段截断，避免超长）。
            error = str(e)
            if failures:
                detail = "; ".join(f"{name}: {msg[:300]}" for name, msg in failures)
                error = f"所有模型均失败（{len(failures)} 个）[{detail[:1200]}]"
                logger.error(f"[LLMAction] 模型回退链全部失败: {error}")
            return ActionResult(
                success=False,
                error=error,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

    def _build_chat_model(
        self,
        *,
        model: str,
        server_url: str,
        api_key: str,
        params: LLMParams,
    ) -> ChatOpenAI:
        """按一份配置构造 ChatOpenAI（主模型与每个备用模型各一份实例）"""
        return ChatOpenAI(
            model=model,
            openai_api_key=api_key,
            openai_api_base=server_url,
            temperature=params.temperature,
            max_tokens=params.max_tokens,
            timeout=params.timeout / 1000.0,
            # 重试交给回退链：主模型失败立刻切备用模型，比原地重试同一个坏模型更快
            max_retries=0,
        )

    @staticmethod
    def _resolve_fallbacks(params: LLMParams) -> list[LLMModelConfig]:
        """备用模型来源（二选一，`fallbacks` 优先）

        - `fallbacks`：动作面板里直接配的数组；
        - `fallbacks_json`：工作流变量注入的 JSON 文本（`{{llm_fallbacks}}` 在步骤执行前
          已被模板替换成字符串）。
        文本解析失败只告警并退化为「单模型」，不因配置写错打断整轮抽奖。
        """
        if params.fallbacks:
            return list(params.fallbacks)
        if not params.fallbacks_json:
            return []
        try:
            return [LLMModelConfig(**item) for item in _parse_model_configs(params.fallbacks_json)]
        except Exception as exc:  # noqa: BLE001 - 配置错误降级为单模型
            logger.warning(
                f"[LLMAction] fallbacks_json 解析失败，退化为单模型: {exc}"
            )
            return []

    def _build_runnable(self, params: LLMParams, failures: list[tuple[str, str]]):
        """构造「主模型 → 备用模型…」回退链（LangChain ``with_fallbacks``）

        - 无备用模型时直接返回主模型的可调用对象（行为与改造前一致）；
        - 有备用模型时，主模型抛异常（429 / 401 / 超时 / 5xx / 连接失败）自动按顺序
          尝试下一个，全部失败则抛出最后一个异常；
        - 结构化输出模式下每个模型先各自 ``with_structured_output``，再组回退链。

        见 ``docs/LLM模型回退计划书.md``。
        """
        fallback_cfgs = self._resolve_fallbacks(params)
        configs: list[tuple[str, str, str]] = [
            (params.model, params.server_url, params.api_key)
        ]
        configs.extend((fb.model, fb.server_url, fb.api_key) for fb in fallback_cfgs)

        runnables: list = []
        for model, server_url, api_key in configs:
            chat = self._build_chat_model(
                model=model,
                server_url=server_url,
                api_key=api_key,
                params=params,
            )
            callable_obj: object = chat
            if params.response_schema:
                schema_model = _create_model_from_schema(params.response_schema)
                callable_obj = chat.with_structured_output(
                    schema_model, method="json_schema"
                )
            runnables.append(_remember_failure(model, callable_obj, failures))

        if len(runnables) == 1:
            return runnables[0]

        logger.info(
            f"[LLMAction] 启用模型回退链: 主={params.model}, "
            f"备用={[fb.model for fb in fallback_cfgs]}"
        )
        return runnables[0].with_fallbacks(
            runnables[1:], exceptions_to_handle=(Exception,)
        )

    async def _call_text(
        self,
        runnable,
        messages: list,
        params: LLMParams,
    ) -> LLMResult:
        """纯文本模式"""
        ai_message: AIMessage = await runnable.ainvoke(messages)
        usage = _extract_usage(ai_message)
        content = (
            ai_message.content
            if isinstance(ai_message.content, str)
            else str(ai_message.content)
        )
        response_model = ai_message.response_metadata.get("model_name", params.model)
        # 尽力而为的启发式：应答模型名与主模型不一致 → 说明用到了备用模型
        fallback_used = bool(params.fallbacks) and response_model != params.model
        if fallback_used:
            logger.info(
                f"[LLMAction] 主模型 {params.model} 失败，已回退到 {response_model}"
            )

        return LLMResult(
            content=content,
            role="assistant",
            model=response_model,
            usage=usage,
            is_structured=False,
            fallback_used=fallback_used,
        )

    async def _call_structured(
        self,
        runnable,
        messages: list,
        params: LLMParams,
    ) -> LLMResult:
        """结构化输出模式：LangChain with_structured_output 自动保证数据符合 schema"""
        ai_result: BaseModel = await runnable.ainvoke(messages)
        structured_data = ai_result.model_dump()
        content_text = json.dumps(structured_data, ensure_ascii=False, indent=2)

        usage = _extract_usage(
            getattr(
                ai_result, "usage_metadata", getattr(ai_result, "response_metadata", {})
            )
        )

        return LLMResult(
            content=content_text,
            role="assistant",
            model=params.model,
            usage=usage,
            is_structured=True,
            structured_data=structured_data,
        )


# 自注册进内置动作注册表（base 的依赖倒置槽位，供控制流策略层按 action_id 查找）

register_action_class(LLMAction)


# ── 工具函数 ───────────────────────────────────────────────


def _remember_failure(model: str, runnable: object, failures: list[tuple[str, str]]):
    """把模型包一层：失败时记录「哪个模型、什么错误」再原样抛出

    ``with_fallbacks`` 只保留第一个异常，不记下备用模型的失败原因，
    导致「主模型 429 / 备用模型 401」这种信息丢失。这里在回退链内逐模型记录，
    上层汇总成一条完整的失败说明。
    """

    async def _run(messages):
        try:
            return await runnable.ainvoke(messages)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - 记完再抛，交给回退链处理
            logger.warning(f"[LLMAction] 模型 {model} 调用失败: {exc}")
            failures.append((model, str(exc)))
            raise

    return RunnableLambda(_run)


def _extract_usage(msg: Any) -> Dict[str, int]:
    """从消息中提取 token 用量"""
    usage: Dict[str, int] = {}

    if hasattr(msg, "usage_metadata") and msg.usage_metadata:
        um = msg.usage_metadata
        usage = {
            "input_tokens": getattr(um, "input_tokens", 0) or 0,
            "output_tokens": getattr(um, "output_tokens", 0) or 0,
            "total_tokens": getattr(um, "total_tokens", 0) or 0,
        }
    elif hasattr(msg, "response_metadata") and msg.response_metadata.get("token_usage"):
        tu = msg.response_metadata["token_usage"]
        usage = {
            "prompt_tokens": tu.get("prompt_tokens", 0),
            "completion_tokens": tu.get("completion_tokens", 0),
            "total_tokens": tu.get("total_tokens", 0),
        }

    return usage
