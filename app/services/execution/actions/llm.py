"""
LLM 类 Action - LLM
"""
import time
import httpx

from app.services.execution.actions.base import BaseAction
from app.models.execution.params import LLMParams
from app.models.core.workflow.models import ActionType, ActionMetadata, ActionResult, ActionContext


class LLMAction(BaseAction):
    """LLM 对话操作"""

    params_model = LLMParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="llm", name="LLM对话", type=ActionType.LLM,
            description="调用 LLM 进行对话，支持 OpenAI 兼容 API",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
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
                final_messages.append({"role": "system", "content": system_prompt})

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
