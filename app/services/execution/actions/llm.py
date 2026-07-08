"""
LLM Action — 基于 LangChain ChatOpenAI，支持结构化输出
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, create_model, Field as PydanticField

from app.services.execution.actions.base import BaseAction, ActionResult
from app.models.execution.action_params import LLMParams, LLMResult
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
        valid, error_msg, validated_params = self.validate_params_with_model(self.params)
        if not valid or not validated_params:
            return ActionResult(
                success=False, error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        prompt = validated_params.prompt
        if not prompt:
            return ActionResult(
                success=False, error="prompt 不能为空",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        messages = [
            SystemMessage(content=validated_params.system_prompt),
            HumanMessage(content=prompt),
        ]

        try:
            chat_model = ChatOpenAI(
                model=validated_params.model,
                openai_api_key=validated_params.api_key,
                openai_api_base=validated_params.server_url,
                temperature=validated_params.temperature,
                max_tokens=validated_params.max_tokens,
                timeout=validated_params.timeout / 1000.0,
                max_retries=0,
            )

            if validated_params.response_schema:
                result = await self._call_structured(chat_model, messages, validated_params)
            else:
                result = await self._call_text(chat_model, messages, validated_params)

            return ActionResult(
                success=True, data=result,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            return ActionResult(
                success=False, error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

    async def _call_text(
        self,
        chat_model: ChatOpenAI,
        messages: list,
        params: LLMParams,
    ) -> LLMResult:
        """纯文本模式"""
        ai_message: AIMessage = await chat_model.ainvoke(messages)
        usage = _extract_usage(ai_message)
        content = ai_message.content if isinstance(ai_message.content, str) else str(ai_message.content)
        response_model = ai_message.response_metadata.get("model_name", params.model)

        return LLMResult(
            content=content, role="assistant", model=response_model,
            usage=usage, is_structured=False,
        )

    async def _call_structured(
        self,
        chat_model: ChatOpenAI,
        messages: list,
        params: LLMParams,
    ) -> LLMResult:
        """结构化输出模式：LangChain with_structured_output 自动保证数据符合 schema"""
        schema_model = _create_model_from_schema(params.response_schema)  # type: ignore[arg-type]
        structured_chat = chat_model.with_structured_output(
            schema_model, method="json_schema",
        )
        ai_result: BaseModel = await structured_chat.ainvoke(messages)
        structured_data = ai_result.model_dump()
        content_text = json.dumps(structured_data, ensure_ascii=False, indent=2)

        usage = _extract_usage(getattr(ai_result, "usage_metadata", getattr(ai_result, "response_metadata", {})))

        return LLMResult(
            content=content_text, role="assistant", model=params.model,
            usage=usage, is_structured=True, structured_data=structured_data,
        )


# ── 工具函数 ───────────────────────────────────────────────

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
