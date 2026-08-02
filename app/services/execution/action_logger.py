"""
浏览器操作日志采集

职责（解耦合，无单独 Recorder 单例）：
    1. resolve_log_option：按优先级解析「本次执行是否采集、采集哪些字段」
       - 执行参数中显式携带的 log 选项（BaseActionParams.log）
       - 父操作透传下来的采集配置（复合/循环/分支控制流的子步骤继承）
       - 自定义操作（ca_xxx）在 CompositeActionModel 上的 log_* 字段
       - 内置操作回落到服务端 settings.action_log_* 兜底
    2. save_action_log：将一次 action 执行的上下文 + 结果落库到 ActionLogRecord

设计原则：
    采集失败绝不影响业务执行 —— save_action_log 内部吞掉所有异常，仅打印告警日志。
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

from app.config import settings
from app.models.database.log.models import (
    ActionLogRecord,
    ActionLogSourceEnum,
    ActionLogStatusEnum,
)
from app.models.execution.action_params import ActionLogOption
from app.services.execution.crud_service import action_crud_svr, action_log_crud_svr


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


async def _resolve_db_log_option(action_id: str) -> Optional[ActionLogOption]:
    """从 CompositeActionModel 的 log_* 字段解析自定义操作的采集配置"""
    cached = _DB_CACHE.get(action_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    option: Optional[ActionLogOption] = None
    try:
        model = await action_crud_svr.get_by_action_id(action_id)
        if model is not None and getattr(model, "log_enabled", False):
            option = ActionLogOption(
                enabled=True,
                record_params=model.log_record_params,
                record_result=model.log_record_result,
                record_variables=model.log_record_variables,
                only_on_error=model.log_only_on_error,
                max_payload_length=model.log_max_payload_length,
                retention_days=model.log_retention_days,
            )
    except Exception:
        logger.warning(f"[ActionLog] 读取采集配置失败，按默认处理: {traceback.format_exc()}")

    _DB_CACHE[action_id] = (time.monotonic() + _DB_CACHE_TTL, option)
    return option


def invalidate_cache(mid: int | str | None = None) -> None:
    """配置变更后清理缓存；按 action 维度缓存，直接全量清理即可"""
    _DB_CACHE.clear()


async def resolve_log_option(
    mid: int | str,
    action_id: str,
    replaced_params: Dict | None = None,
    ctx_log_config: Optional[ActionLogOption] = None,
) -> Optional[ActionLogOption]:
    """解析生效的日志采集配置（优先级从高到低）。

    返回 None 表示「本次不采集」（包括显式关闭 / 未启用）。
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

    # 3. 自定义操作（ca_xxx）在数据库上的 log_* 配置
    if action_id.startswith("ca_"):
        return await _resolve_db_log_option(action_id)

    # 4. 内置操作回落到服务端兜底配置
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
            logger.warning(f"[ActionLog] 写入操作日志失败({ctx.action_id}): {traceback.format_exc()}")


async def _do_save(
    ctx: ActionLogContext,
    result: Any,
    *,
    status: ActionLogStatusEnum | None = None,
) -> None:
    success = bool(getattr(result, "success", False))
    option = await resolve_log_option(ctx.mid, ctx.action_id, ctx.params, ctx.log_config)
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
        action_id=ctx.action_id,
        action_name=ctx.action_name or ctx.action_id,
        action_type=ctx.action_type or ctx.action_id,
        source=ctx.source,
        workflow_id=ctx.workflow_id,
        browser_id=ctx.browser_id,
        session_id=ctx.session_id,
        page_url=_safe_page_url(ctx.page),
        status=final_status,
        success=success,
        params=_safe_jsonify(ctx.params) if option.record_params else None,
        result_data=(
            _safe_jsonify({
                "success": success,
                "data": getattr(result, "data", None),
            })
            if option.record_result
            else None
        ),
        variables=_safe_jsonify(ctx.variables) if option.record_variables else None,
        logs=_safe_jsonify(getattr(result, "logs", None)),
        error_message=str(getattr(result, "error", None) or "") or None,
        execution_time=float(getattr(result, "execution_time", 0.0) or 0.0),
        started_at=ctx.started_at,
        finished_at=datetime.now(),
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
        action_id=ctx.action_id,
        action_name=ctx.action_name or ctx.action_id,
        action_type=ctx.action_type or ctx.action_id,
        source=ctx.source,
        workflow_id=ctx.workflow_id,
        browser_id=ctx.browser_id,
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


def _safe_jsonify(value: Any) -> Dict | None:
    """将任意值转为可存 JSON 列的 dict，失败则返回包含错误信息的 dict"""
    if value is None:
        return None
    try:
        data = _to_jsonable(value)
        if not isinstance(data, dict):
            data = {"value": data}
        return data
    except Exception:
        return {"_serialization_error": str(traceback.format_exc())}


def _to_jsonable(value: Any, depth: int = 0) -> Any:
    """递归将任意对象转为可 JSON 序列化的结构"""
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        try:
            return _to_jsonable(value.model_dump(), depth + 1)
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v, depth + 1) for v in value]
    if callable(value):
        return f"<callable {getattr(value, '__name__', 'anonymous')}>"
    return str(value)


__all__ = [
    "ActionLogContext",
    "ActionLogOption",
    "resolve_log_option",
    "save_action_log",
    "new_execution_id",
    "invalidate_cache",
]
