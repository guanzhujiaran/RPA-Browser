"""
浏览器操作日志采集

职责（解耦合，无单独 Recorder 单例）：
    1. resolve_log_option：按优先级解析「本次执行是否采集、采集哪些字段」。
       三级开关均为三态（None=未设置，听从下一级；True/False=显式开/关），
       优先级从高到低：
           1) 执行参数中显式携带的 log 选项（BaseActionParams.log）
           2) 父操作透传下来的已解析配置（复合/循环/分支子步骤继承）
           3) 工作流开关（UserWorkflow.log_enabled，仅工作流执行时传入）
           4) 动作开关（CompositeActionModel.log_enabled）
           5) 项目兜底（settings.action_log_default_enabled）
    2. save_action_log：将一次 action 执行的上下文 + 结果落库到 ActionLogRecord

设计原则：
    采集失败绝不影响业务执行 —— save_action_log 内部吞掉所有异常，仅打印告警日志；
    落库默认走 RabbitMQ 异步消费（action_log_use_mq），投递失败熔断回落同步直写。
"""

from __future__ import annotations

import contextlib
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger

from bili_common.core.log_pub import (
    ACTION_LOG_SCHEMA_VERSION,
    ActionLogMessage,
    publish_action_log,
)

from app.config import settings
from app.models.database.log.models import (
    ActionLogRecord,
    ActionLogSourceEnum,
    ActionLogStatusEnum,
)
from app.models.execution.action_params import ActionLogOption
from app.models.execution.action_params import is_composite_action_id
from app.services.execution.crud_service import action_crud_svr, action_log_crud_svr
from app.utils.json_utils import jsonify, shrink_json_payload


def new_execution_id() -> str:
    """生成一次执行批次ID"""
    return uuid.uuid4().hex


# 服务端兜底配置（内置操作 / 未配置时使用）
_FALLBACK_CONFIG = ActionLogOption(
    enabled=settings.action_log_default_enabled,
    record_params=True,
    record_result=True,
    record_variables=False,
    only_on_error=False,
    max_payload_length=settings.action_log_max_payload_length,
    retention_days=settings.action_log_default_retention_days,
)


# ca_ 操作 DB 配置解析缓存（按 action 维度，避免子步骤高频查库）
_DB_CACHE: Dict[str, tuple[float, Optional[ActionLogOption]]] = {}
_DB_CACHE_TTL = 30.0

# MQ 投递熔断截止时间（monotonic 秒）：投递失败后进入冷却期，期间直接同步直写，
# 避免队列不可用时每条日志都触发一次 broker 建连等待、拖死 RPA 执行线程
_MQ_COOLDOWN_UNTIL: float = 0.0


async def _resolve_db_log_option(action_id: str) -> Optional[ActionLogOption]:
    """从 CompositeActionModel 的 log_* 字段解析自定义操作的采集配置（三态）。

    返回 None 表示「未设置」（模型缺失 / log_enabled 为 NULL），由调用方继续向
    下一级（项目兜底）解析；返回 option 时其 enabled 为显式布尔值。
    """
    cached = _DB_CACHE.get(action_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    option: Optional[ActionLogOption] = None
    try:
        model = await action_crud_svr.get_by_action_id(action_id)
        if model is not None and getattr(model, "log_enabled", None) is not None:
            option = ActionLogOption(
                enabled=bool(model.log_enabled),
                record_params=model.log_record_params,
                record_result=model.log_record_result,
                record_variables=model.log_record_variables,
                only_on_error=model.log_only_on_error,
                max_payload_length=model.log_max_payload_length,
                retention_days=model.log_retention_days,
            )
    except Exception:
        logger.warning(
            f"[ActionLog] 读取采集配置失败，按默认处理: {traceback.format_exc()}"
        )

    _DB_CACHE[action_id] = (time.monotonic() + _DB_CACHE_TTL, option)
    return option


async def _resolve_action_option(action_id: str) -> Optional[ActionLogOption]:
    """动作级采集配置：仅复合动作可能有显式配置；None 表示未设置。"""
    if not is_composite_action_id(action_id):
        return None
    return await _resolve_db_log_option(action_id)


def _with_enabled(
    option: Optional[ActionLogOption], enabled: bool
) -> ActionLogOption:
    """以 option 的字段级配置为基底，仅覆盖 enabled；option 为空时用兜底字段配置。"""
    base = option if option is not None else _FALLBACK_CONFIG
    return base.model_copy(update={"enabled": enabled})


def invalidate_cache(mid: int | str | None = None) -> None:
    """配置变更后清理缓存；按 action 维度缓存，直接全量清理即可"""
    _DB_CACHE.clear()


async def resolve_log_option(
    mid: int | str,
    action_id: str,
    replaced_params: Dict | None = None,
    ctx_log_config: Optional[ActionLogOption] = None,
    workflow_log_enabled: bool | None = None,
) -> Optional[ActionLogOption]:
    """解析生效的日志采集配置（优先级从高到低，开关均为三态）。

    任一级显式取值（True/False）即终止解析；None（未设置）继续向下一级。

    1. 执行参数中显式携带的 log 选项（BaseActionParams.log）；
    2. 父操作透传的已解析配置（复合/循环/分支控制流的子步骤继承）；
    3. 工作流开关 `workflow_log_enabled`（仅工作流执行时由 engine 传入）：
       本次运行的总闸，显式开/关都压制动作级与项目级；字段级配置仍取自动作自身；
    4. 动作开关（CompositeActionModel.log_enabled，三态）；
    5. 项目兜底（settings.action_log_default_enabled，内置操作也回落至此）。

    返回 None 表示「本次不采集」（含解析失败降级）。
    """
    # 1. 执行参数中显式携带的 log 选项（BaseActionParams.log）
    if isinstance(replaced_params, dict):
        raw = replaced_params.get("log")
        if isinstance(raw, ActionLogOption):
            return raw
        if isinstance(raw, dict):
            with contextlib.suppress(Exception):
                return ActionLogOption.model_validate(raw)

    # 2. 父操作透传下来的采集配置（复合/循环/分支控制流的子步骤继承）
    if ctx_log_config is not None:
        return ctx_log_config

    # 3. 工作流级开关（本次运行的总闸；显式开/关压制动作级与项目级）
    if workflow_log_enabled is not None:
        return _with_enabled(
            await _resolve_action_option(action_id), bool(workflow_log_enabled)
        )

    # 4. 动作级开关（三态；None=未设置时继续向项目兜底解析）
    action_option = await _resolve_action_option(action_id)
    if action_option is not None:
        return action_option

    # 5. 内置操作 / 均未设置 → 项目兜底
    return _FALLBACK_CONFIG


@dataclass
class ActionLogContext:
    """一次 action 执行的采集上下文"""

    mid: int | str
    action_id: str
    action_name: str = ""
    action_type: str = ""
    source: ActionLogSourceEnum = ActionLogSourceEnum.ACTION
    execution_id: str = ""
    parent_execution_id: str | None = None
    depth: int = 0
    workflow_id: str | None = None
    browser_id: str = ""
    session_id: str = ""
    page: Any = None
    params: Dict | None = None
    variables: Dict | None = None
    started_at: datetime = field(default_factory=datetime.now)
    # 复合操作内部子步骤继承的采集配置；为空时按 action_id 自行解析
    log_config: Optional[ActionLogOption] = None


async def save_action_log(
    ctx: ActionLogContext,
    result: Any,
    *,
    status: ActionLogStatusEnum | None = None,
) -> None:
    """根据生效的 log 选项，将一次 action 执行结果落库。

    内部吞掉所有异常，绝不影响业务执行。
    """
    try:
        await _do_save(ctx, result, status=status)
    except Exception:
        # 兜底：如果上面抛了异常，尝试写入一条"保存失败"的错误记录
        try:
            await _save_fallback(ctx, str(traceback.format_exc()))
        except Exception:
            logger.warning(
                f"[ActionLog] 写入操作日志失败({ctx.action_id}): {traceback.format_exc()}"
            )


async def _do_save(
    ctx: ActionLogContext,
    result: Any,
    *,
    status: ActionLogStatusEnum | None = None,
) -> None:
    success = bool(getattr(result, "success", False))
    option = await resolve_log_option(
        ctx.mid, ctx.action_id, ctx.params, ctx.log_config
    )
    if option is None or not option.enabled:
        return
    if option.only_on_error and success:
        return

    final_status = status or (
        ActionLogStatusEnum.SUCCESS if success else ActionLogStatusEnum.FAILED
    )

    record = ActionLogRecord(
        log_id=uuid.uuid4().hex,
        mid=str(ctx.mid),
        execution_id=ctx.execution_id or new_execution_id(),
        parent_execution_id=ctx.parent_execution_id,
        depth=ctx.depth,
        action_id=str(ctx.action_id),
        action_name=str(ctx.action_name or ctx.action_id),
        action_type=str(ctx.action_type or ctx.action_id),
        source=ctx.source,
        workflow_id=str(ctx.workflow_id) if ctx.workflow_id is not None else None,
        browser_id=str(ctx.browser_id) if ctx.browser_id is not None else "",
        session_id=ctx.session_id,
        page_url=_safe_page_url(ctx.page),
        status=final_status,
        success=success,
        params=(
            shrink_json_payload(
                _redact_sensitive(jsonify(ctx.params)),
                option.max_payload_length,
            )
            if option.record_params
            else None
        ),
        result_data=(
            shrink_json_payload(
                _redact_sensitive(
                    jsonify(
                        {
                            "success": success,
                            "data": getattr(result, "data", None),
                        }
                    )
                ),
                option.max_payload_length,
            )
            if option.record_result
            else None
        ),
        variables=(
            shrink_json_payload(
                _redact_sensitive(jsonify(ctx.variables)),
                option.max_payload_length,
            )
            if option.record_variables
            else None
        ),
        logs=normalize_logs(getattr(result, "logs", None)),
        error_message=str(getattr(result, "error", None) or "") or None,
        execution_time=float(getattr(result, "execution_time", 0.0) or 0.0),
        started_at=ctx.started_at,
        finished_at=datetime.now(),
    )
    await _persist(record)


async def _persist(record: ActionLogRecord) -> None:
    """落库一次操作日志。

    MQ 开关开启时优先投递队列（把日志落库与业务执行解耦，执行协程只承担一次
    fire-and-forget 投递，不同步等待落库），投递失败则回落为同步直写，
    保证「日志不因队列不可用而丢失」；开关关闭时行为与改造前完全一致。

    熔断：投递失败后进入 `settings.action_log_mq_cooldown_seconds` 冷却期，
    冷却期内直接同步直写——避免队列不可用时每条日志都触发一次 broker 建连等待，
    把 RPA 执行线程拖死；冷却结束自动重试投递（恢复后回到 MQ 异步落库）。
    """
    global _MQ_COOLDOWN_UNTIL
    if settings.action_log_use_mq and time.monotonic() >= _MQ_COOLDOWN_UNTIL:
        try:
            await publish_action_log(
                ActionLogMessage(
                    schema_version=ACTION_LOG_SCHEMA_VERSION,
                    record=record.model_dump(mode="json"),
                ),
                amqp_url=settings.rabbitmq_url,
            )
            return
        except Exception:
            _MQ_COOLDOWN_UNTIL = (
                time.monotonic() + settings.action_log_mq_cooldown_seconds
            )
            logger.warning(
                f"[ActionLog] 投递日志队列失败，进入 {settings.action_log_mq_cooldown_seconds}s "
                f"熔断（期间同步直写）: {traceback.format_exc()}"
            )
    await action_log_crud_svr.create(record)


async def _save_fallback(ctx: ActionLogContext, error_trace: str) -> None:
    """当正常记录失败时，写入一条"保存失败"的兜底记录"""
    record = ActionLogRecord(
        log_id=uuid.uuid4().hex,
        mid=str(ctx.mid),
        execution_id=ctx.execution_id or new_execution_id(),
        parent_execution_id=ctx.parent_execution_id,
        depth=ctx.depth,
        action_id=str(ctx.action_id),
        action_name=str(ctx.action_name or ctx.action_id),
        action_type=str(ctx.action_type or ctx.action_id),
        source=ctx.source,
        workflow_id=str(ctx.workflow_id) if ctx.workflow_id is not None else None,
        browser_id=str(ctx.browser_id) if ctx.browser_id is not None else "",
        session_id=ctx.session_id,
        page_url=_safe_page_url(ctx.page),
        status=ActionLogStatusEnum.FAILED,
        success=False,
        error_message=f"[LogSaveFailed] {error_trace}",
        execution_time=0.0,
        started_at=ctx.started_at,
        finished_at=datetime.now(),
    )
    await action_log_crud_svr.create(record)


# ═══════════════ 序列化工具 ═══════════════════════


def _safe_page_url(page: Any) -> str:
    try:
        return str(getattr(page, "url", "") or "")
    except Exception:
        return ""


# 敏感字段关键词黑名单（按 key 大小写不敏感的子串匹配）
_SENSITIVE_KEYWORDS = (
    "llm_api_key",
    "llm_server_url",
    "token",
    "password",
    "secret",
    "api_key",
)
_REDACTED_VALUE = "***"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(kw in lowered for kw in _SENSITIVE_KEYWORDS)


def _redact_sensitive(value: Any) -> Any:
    """递归脱敏：key 命中黑名单的字段值替换为 `***`。

    脱敏在**生产端**就地完成（绝不「全量投递 + 消费端过滤」），
    避免 `llm_api_key` 等敏感信息进入消息队列或落库。
    """
    if isinstance(value, dict):
        return {
            str(k): (
                _REDACTED_VALUE if _is_sensitive_key(str(k)) else _redact_sensitive(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive(v) for v in value]
    return value


def normalize_logs(value: Any) -> list[str]:
    """将执行过程日志规范为字符串列表。

    `logs` 列的模型类型是 List[str]，但历史上曾误用 `jsonify` 写入，
    导致列表被包成 {"value": [...]}；此函数同时兼容该脏数据。
    """
    if isinstance(value, dict) and set(value.keys()) == {"value"}:
        value = value["value"]
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v if isinstance(v, str) else str(v) for v in value]
    return [str(value)]


__all__ = [
    "ActionLogContext",
    "ActionLogOption",
    "resolve_log_option",
    "save_action_log",
    "new_execution_id",
    "invalidate_cache",
]
