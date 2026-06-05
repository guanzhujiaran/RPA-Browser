"""
LLM 类 Action - LLM
"""
from typing import Dict, Any, List

import time
import httpx

from app.services.execution.actions.base import BaseAction
from app.models.execution.action_params import LLMParams
from app.models.database.workflow.models import ActionResult
from app.models.database.workflow.models import BuiltinActionType


class LLMAction(BaseAction):
    """LLM 对话操作"""
    action_id: BuiltinActionType = BuiltinActionType.LLM
    params: LLMParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict[str, Any], params: LLMParams | None = None, timeout: int = 30000, input_vars: Dict[str, Any] | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        return super().new_action(
            mid=mid, page=page, variables=variables,
            params=params, timeout=timeout,
            input_vars=input_vars, output_vars=output_vars,
            action_name=action_name,
        )

    async def execute(self) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        server_url = validated_params.server_url
        api_key = validated_params.api_key
        model = validated_params.model
        messages = validated_params.messages
        prompt = validated_params.prompt
        system_prompt = validated_params.system_prompt
        temperature = validated_params.temperature
        max_tokens = validated_params.max_tokens
        timeout = validated_params.timeout

        try:
            # 构建消息列表
            final_messages = []

            if system_prompt:
                final_messages.append(
                    {"role": "system", "content": system_prompt})

            for msg in messages:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    final_messages.append(msg)

            if prompt:
                final_messages.append({"role": "user", "content": prompt})

            if not final_messages:
                return ActionResult(
                    success=False, error="messages 或 prompt 不能同时为空",
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id, action_name=self.metadata.name,
                )

            # 构建请求
            endpoint = f"{server_url.rstrip('/')}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": final_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            async with httpx.AsyncClient(timeout=timeout / 1000) as client:
                response = await client.post(endpoint, json=payload, headers=headers)

                if response.status_code != 200:
                    error_detail = response.text
                    return ActionResult(
                        success=False,
                        error=f"API 请求失败 ({response.status_code}): {error_detail}",
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id, action_name=self.metadata.name,
                    )

                result_data = response.json()

            # 解析响应
            if "choices" not in result_data or not result_data["choices"]:
                return ActionResult(
                    success=False, error="API 响应格式异常，未找到 choices",
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id, action_name=self.metadata.name,
                )

            choice = result_data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content", "")
            role = message.get("role", "assistant")

            response_data = {
                "content": content, "role": role, "model": model,
                "usage": result_data.get("usage", {}),
                "raw_response": result_data,
                "text": content, "answer": content, "result": content,
            }

            return ActionResult(
                success=True, data=response_data,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except httpx.TimeoutException:
            return ActionResult(
                success=False, error=f"请求超时 ({timeout}ms)",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
        except httpx.RequestError as e:
            return ActionResult(
                success=False, error=f"请求失败: {str(e)}",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
        except Exception as e:
            return ActionResult(
                success=False, error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
