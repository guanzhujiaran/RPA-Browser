"""
LLM Action — 基于 LangChain ChatOpenAI，支持结构化输出
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Dict, List
from urllib.parse import urlparse

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
        failures: list[_LLMFailure] = []
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
                error = f"{error}；已失败模型：{_format_llm_failures(failures)}"
            return ActionResult(
                success=False,
                error=error,
                logs=_failure_logs(failures),
                error_details=_failure_details(failures),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            # `with_fallbacks` 全失败时只抛出**第一个**异常（langchain 内部 `raise
            # first_error`），备用模型的失败原因会丢，因此用包装层收集到的 failures
            # 拼一条完整说明：每项带「端点 + 序号 + 错误类别」，超出条数按类别计数。
            error = str(e)
            if failures:
                error = (
                    f"所有模型均失败（{len(failures)} 个）："
                    f"{_format_llm_failures(failures)}"
                )
                logger.error(f"[LLMAction] 模型回退链全部失败: {error}")
            return ActionResult(
                success=False,
                error=error,
                logs=_failure_logs(failures),
                error_details=_failure_details(failures),
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

    def _build_runnable(self, params: LLMParams, failures: list[_LLMFailure]):
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
        for index, (model, server_url, api_key) in enumerate(configs, start=1):
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
            runnables.append(
                _remember_failure(
                    model,
                    callable_obj,
                    failures,
                    index=index,
                    endpoint=_endpoint_of(server_url),
                )
            )

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


@dataclass(frozen=True)
class _LLMFailure:
    """一次模型调用的失败现场（用于「全部失败」时的完整汇报）。

    ``with_fallbacks`` 内部只保留第一个异常（``langchain_core/runnables/
    fallbacks.py`` 里的 ``raise first_error``），备用模型的异常会被静默吞掉；
    因此只能在回退链的每一环外面包一层，把失败逐个记下来。这里固定三项：

    - ``label``：``模型@端点#序号``。LLM 列表里常见「同名模型 + 多个 key/中转站」，
      只记模型名无法区分到底哪个配置挂了；
    - ``kind``：错误类别（429 限流 / 401 鉴权 / 超时 / 连接失败 …），便于快速判断
      是「配额用尽」还是「key 写错」；
    - ``message``：脱敏 + 截断后的原始错误摘要。
    """

    label: str
    kind: str
    message: str


#: 错误文本 → 类别的兜底匹配表（HTTP 状态在 _classify_llm_error 里优先处理）
_LLM_ERROR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("429 限流", ("429", "rate limit", "rate_limit", "too many requests", "quota")),
    (
        "401 鉴权失败",
        ("401", "unauthorized", "invalid api key", "invalid_api_key", "authentication"),
    ),
    ("403 无权限", ("403", "forbidden", "permission denied", "no permission")),
    ("404 模型或端点不存在", ("404", "not found", "model_not_found", "does not exist")),
    (
        "5xx 服务端错误",
        ("500", "502", "503", "504", "internal server error", "bad gateway"),
    ),
    (
        "连接失败",
        ("connection", "connect", "econnrefused", "getaddrinfo", "network", "proxy"),
    ),
    ("超时", ("timeout", "timed out", "read timeout")),
)

#: 常见密钥形态（日志/通知里一律脱敏）
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{6,}"), "sk-***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1***"),
    (re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{8,}"), r"\1***"),
)


def _endpoint_of(server_url: str | None) -> str:
    """取端点标识（host:port），不把完整 URL/query 带进通知。"""
    if not server_url:
        return "-"
    parsed = urlparse(server_url if "://" in server_url else f"http://{server_url}")
    return parsed.netloc or server_url


def _scrub_secrets(text: str) -> str:
    """把错误文本里的密钥打码（异常里偶尔会带出 Authorization / api_key）。"""
    cleaned = text
    for pattern, replacement in _SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def _classify_llm_error(exc: BaseException) -> str:
    """给异常归类（优先 status_code，其次异常类型，最后文本关键字）。"""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "超时"

    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        if status == 429:
            return "429 限流"
        if status == 401:
            return "401 鉴权失败"
        if status == 403:
            return "403 无权限"
        if status == 404:
            return "404 模型或端点不存在"
        if 500 <= status < 600:
            return "5xx 服务端错误"
        return f"HTTP {status}"

    text = str(exc).lower()
    for kind, needles in _LLM_ERROR_KEYWORDS:
        if any(needle in text for needle in needles):
            return kind
    return "调用失败"


def _format_llm_failures(
    failures: list[_LLMFailure],
    *,
    limit: int = 8,
    max_length: int = 1200,
) -> str:
    """把逐模型失败汇总成一行可读文本。

    - 最多展开 ``limit`` 条（每条含端点与类别），其余按类别计数，避免被长度截断后
      「后面的错误全部消失」；
    - 总长再兜一层 ``max_length``，防止单条超长报错（整页 HTML）把通知撑爆。
    """
    if not failures:
        return ""
    parts = [f"{f.label} {f.kind}：{f.message}" for f in failures[:limit]]
    rest = failures[limit:]
    if rest:
        counter = Counter(f.kind for f in rest)
        summary = "、".join(f"{kind}×{count}" for kind, count in counter.most_common())
        parts.append(f"其余 {len(rest)} 条：{summary}")
    text = "；".join(parts)
    return text if len(text) <= max_length else f"{text[:max_length]}…"


def _failure_logs(failures: list[_LLMFailure]) -> list[str]:
    """逐条失败日志（全量、不省略），供动作日志留痕与前端展开排查。"""
    return [f"LLM 模型失败 {f.label}（{f.kind}）：{f.message}" for f in failures]


def _failure_details(failures: list[_LLMFailure]) -> dict[str, Any] | None:
    """失败的结构化明细（写入 `ActionResult.error_details` → 动作日志 result_data）。

    `error` 是给人/通知看的文本摘要，超过列宽还会被截断；这里是机器可读版本，
    前端可据此按类别聚合（「3 个 401、2 个 429」）而不必解析字符串。
    """
    if not failures:
        return None
    return {
        "model_count": len(failures),
        "llm_failures": [asdict(item) for item in failures],
    }


def _remember_failure(
    model: str,
    runnable: object,
    failures: list[_LLMFailure],
    *,
    index: int = 1,
    endpoint: str = "-",
):
    """把模型包一层：失败时记录「哪个配置、什么类别、什么错误」再原样抛出

    ``with_fallbacks`` 只抛出第一个异常、不保留备用模型的失败原因
    （「主模型 429 / 备用模型 401」这种现场会只剩 429）。这里在回退链内逐模型记录，
    上层汇总成一条完整说明。
    """
    label = f"{model}@{endpoint}#{index}"

    async def _run(messages):
        try:
            return await runnable.ainvoke(messages)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - 记完再抛，交给回退链处理
            kind = _classify_llm_error(exc)
            message = " ".join(_scrub_secrets(str(exc)).split())[:200]
            failures.append(_LLMFailure(label=label, kind=kind, message=message))
            logger.warning(f"[LLMAction] 模型 {label} 调用失败（{kind}）: {message}")
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
