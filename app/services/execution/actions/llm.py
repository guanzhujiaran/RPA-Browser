"""
LLM 类 Action - 基于 LangChain ChatOpenAI
"""
from typing import Dict, Any, List
import time

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from app.services.execution.actions.base import BaseAction, ActionResult
from app.models.execution.action_params import LLMParams, LLMResult
from app.models.database.workflow.models import BuiltinActionType


class LLMAction(BaseAction[LLMParams]):
    """LLM 对话操作（基于 LangChain ChatOpenAI）"""
    action_id: BuiltinActionType = BuiltinActionType.LLM
    action_type: BuiltinActionType = BuiltinActionType.LLM
    params: LLMParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: LLMParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[LLMResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid or not validated_params:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        server_url = validated_params.server_url
        api_key = validated_params.api_key
        model = validated_params.model
        prompt = validated_params.prompt
        system_prompt = validated_params.system_prompt
        temperature = validated_params.temperature
        max_tokens = validated_params.max_tokens
        timeout_sec = validated_params.timeout / 1000.0

        try:
            if not prompt:
                return ActionResult(
                    success=False, error="prompt不能为空",
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id, action_name=self.metadata.name,
                )
            system_msg = SystemMessage(content=system_prompt)
            human_msg = HumanMessage(content=prompt)
            messages = [system_msg, human_msg]
            chat_model = ChatOpenAI(
                model=model,
                openai_api_key=api_key,
                openai_api_base=server_url,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout_sec,
                max_retries=0,
            )

            ai_message: AIMessage = await chat_model.ainvoke(messages)

            # 提取 token 用量
            usage: Dict = {}
            if hasattr(ai_message, 'usage_metadata') and ai_message.usage_metadata:
                um = ai_message.usage_metadata
                usage = {
                    "input_tokens": getattr(um, 'input_tokens', 0) or 0,
                    "output_tokens": getattr(um, 'output_tokens', 0) or 0,
                    "total_tokens": getattr(um, 'total_tokens', 0) or 0,
                }
            elif ai_message.response_metadata.get("token_usage"):
                tu = ai_message.response_metadata["token_usage"]
                usage = {
                    "prompt_tokens": tu.get("prompt_tokens", 0),
                    "completion_tokens": tu.get("completion_tokens", 0),
                    "total_tokens": tu.get("total_tokens", 0),
                }

            content = ai_message.content if isinstance(
                ai_message.content, str) else str(ai_message.content)
            response_model = ai_message.response_metadata.get(
                "model_name", model)

            response_data = {
                "content": content,
                "role": "assistant",
                "model": response_model,
                "usage": usage,
                "raw_response": ai_message,
            }

            return ActionResult(
                success=True, data=LLMResult(**response_data),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            error_msg = str(e)
            return ActionResult(
                success=False, error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
