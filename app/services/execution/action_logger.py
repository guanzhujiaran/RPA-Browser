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
import json
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


def new_execution_id() -> str:
    """生成一次执行批次ID"""
    return uuid.uuid4().hex


#: `actionlogrecord.error_message` 的列宽（varchar(2000)）。
#: 直接从模型读，避免模型改宽后这里忘记同步。
_ERROR_MESSAGE_MAX_LENGTH: int = int(
    getattr(ActionLogRecord.model_fields["error_message"], "max_length", None) or 2000
)


def _truncate_error_message(value: Any) -> str | None:
    """把失败原因截到列宽以内。

    `error_message` 是 ``varchar(2000)``：超长在 MySQL 严格模式下会
    ``Data too long for column``，让**整条日志**写入失败（``_save_fallback`` 同样会
    失败），最后只剩一行 warning，排查线索彻底丢失。宁可截断也要先存下来。
    """
    text = str(value or "")[:_ERROR_MESSAGE_MAX_LENGTH]
    return text or None


def _build_result_payload(result: Any, success: bool) -> Dict[str, Any]:
    """构造 ``result_data`` 列的内容。

    基础是 ``{success, data}``；若动作提供了 ``error_details``（如 LLM 回退链的逐模型
    失败列表），一并带上，让「按类别统计失败原因」不必解析 ``error_message`` 文本。
    """
    payload: Dict[str, Any] = {
        "success": success,
        "data": getattr(result, "data", None),
    }
    error_details = getattr(result, "error_details", None)
    if error_details:
        payload["error_details"] = error_details
    return payload


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

    返回 None 表示「未设置」（模型缺失 / log_enabled 为 NULL 且未开 debug），
    由调用方继续向下一级（项目兜底）解析；返回 option 时其 enabled 为显式
    布尔值。注意 ``log_debug=True`` 独立生效：调试模式隐含「开启」，
    即使 ``log_enabled`` 未设置也走动作级配置（归一化见 _normalize_debug）。
    """
    cached = _DB_CACHE.get(action_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    option: Optional[ActionLogOption] = None
    try:
        model = await action_crud_svr.get_by_action_id(action_id)
        if model is not None and (
            getattr(model, "log_enabled", None) is not None
            or bool(getattr(model, "log_debug", False))
        ):
            debug = bool(getattr(model, "log_debug", False))
            option = ActionLogOption(
                enabled=bool(model.log_enabled) or debug,
                debug=debug,
                record_params=model.log_record_params,
                record_result=model.log_record_result,
                record_variables=model.log_record_variables,
                record_page_snapshot=bool(
                    getattr(model, "log_record_page_snapshot", False)
                ),
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


def _normalize_debug(option: Optional[ActionLogOption]) -> Optional[ActionLogOption]:
    """debug 模式归一化（用户级调试开关，见调度外壳计划书 §9.8）。

    `debug=True` 时强制全量采集：入参 / 结果 / 变量全记、成功也记、
    payload 不截断（`max_payload_length=0`）；保留期与普通日志一致，
    不做单独管理。幂等：已归一化的配置再走一次不变。
    """
    if option is None or not option.debug:
        return option
    return option.model_copy(
        update={
            "enabled": True,
            "record_params": True,
            "record_result": True,
            "record_variables": True,
            "record_page_snapshot": True,
            "only_on_error": False,
            "max_payload_length": 0,
        }
    )


async def _resolve_log_option_inner(
    mid: int | str,
    action_id: str,
    replaced_params: Dict | None = None,
    ctx_log_config: Optional[ActionLogOption] = None,
    workflow_log_enabled: bool | None = None,
    workflow_log_debug: bool | None = None,
) -> Optional[ActionLogOption]:
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

    # 3. 工作流级调试模式（用户在工作流上开启的 log_debug）：
    #    优先于工作流开关——debug 隐含「开启」，强制全量采集，
    #    压制动作级与项目级（见 _normalize_debug）
    if workflow_log_debug:
        return _FALLBACK_CONFIG.model_copy(update={"enabled": True, "debug": True})

    # 4. 工作流级开关（本次运行的总闸；显式开/关压制动作级与项目级）
    if workflow_log_enabled is not None:
        return _with_enabled(
            await _resolve_action_option(action_id), bool(workflow_log_enabled)
        )

    # 5. 动作级开关（三态；None=未设置时继续向项目兜底解析）
    action_option = await _resolve_action_option(action_id)
    if action_option is not None:
        return action_option

    # 6. 内置操作 / 均未设置 → 项目兜底
    return _FALLBACK_CONFIG


async def resolve_log_option(
    mid: int | str,
    action_id: str,
    replaced_params: Dict | None = None,
    ctx_log_config: Optional[ActionLogOption] = None,
    workflow_log_enabled: bool | None = None,
    workflow_log_debug: bool | None = None,
) -> Optional[ActionLogOption]:
    """解析生效的日志采集配置（优先级从高到低，开关均为三态）。

    任一级显式取值（True/False）即终止解析；None（未设置）继续向下一级。

    1. 执行参数中显式携带的 log 选项（BaseActionParams.log）；
    2. 父操作透传的已解析配置（复合/循环/分支控制流的子步骤继承）；
    3. 工作流级调试模式 `workflow_log_debug`（用户在工作流上开启）：
       优先于工作流开关，debug 隐含「开启」并强制全量采集；
    4. 工作流开关 `workflow_log_enabled`（仅工作流执行时由 engine 传入）：
       本次运行的总闸，显式开/关都压制动作级与项目级；字段级配置仍取自动作自身；
    5. 动作开关（CompositeActionModel.log_enabled / log_debug，三态）；
    6. 项目兜底（settings.action_log_default_enabled，内置操作也回落至此）。

    任一级带 `debug=True`（用户级调试模式）时，最终配置归一化为全量采集
    （入参/结果/变量全记、成功也记、payload 不截断，见 `_normalize_debug`）。

    返回 None 表示「本次不采集」（含解析失败降级）。
    """
    option = await _resolve_log_option_inner(
        mid,
        action_id,
        replaced_params,
        ctx_log_config,
        workflow_log_enabled,
        workflow_log_debug,
    )
    return _normalize_debug(option)


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
    force: bool = False,
) -> None:
    """根据生效的 log 选项，将一次 action 执行结果落库。

    Args:
        force: 强制执行留痕（即使动作 / 工作流关了日志）。用于**失败**场景——
            失败必须可回溯，否则 `on_error=continue` 把失败降级成「成功」后，
            关着日志的运行为「为什么没干活」留下零线索。

    内部吞掉所有异常，绝不影响业务执行。
    """
    try:
        await _do_save(ctx, result, status=status, force=force)
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
    force: bool = False,
) -> None:
    success = bool(getattr(result, "success", False))
    option = await resolve_log_option(
        ctx.mid, ctx.action_id, ctx.params, ctx.log_config
    )
    if option is None or not option.enabled:
        if not force:
            return
        # 失败强制留痕：日志开关只应该影响「成功」的记录量，不能连失败一起吞掉。
        # 记什么字段仍沿用原配置（option 为空时用项目兜底），只把 enabled 打开。
        option = _with_enabled(option, True)
    if option.only_on_error and success:
        return

    final_status = status or (
        ActionLogStatusEnum.SUCCESS if success else ActionLogStatusEnum.FAILED
    )

    # 页面快照只在「失败 + 开关开」时采集：成功没有排查价值，且体积远大于其他字段
    page_snapshot = (
        await _capture_page_snapshot(ctx.page, option.max_payload_length)
        if (option.record_page_snapshot and not success)
        else None
    )

    # 结果载荷：成功/失败 + data；失败时附带结构化明细（如 LLM 逐模型失败列表），
    # 便于按字段统计「几个 401、几个 429」，不用去解析 error_message 文本
    result_payload: Dict[str, Any] = _build_result_payload(result, success)

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
            _truncate_payload(
                _redact_sensitive(_safe_jsonify(ctx.params)),
                option.max_payload_length,
            )
            if option.record_params
            else None
        ),
        result_data=(
            _truncate_payload(
                _redact_sensitive(_safe_jsonify(result_payload)),
                option.max_payload_length,
            )
            if option.record_result
            else None
        ),
        variables=(
            _truncate_payload(
                _redact_sensitive(_safe_jsonify(ctx.variables)),
                option.max_payload_length,
            )
            if option.record_variables
            else None
        ),
        page_snapshot=page_snapshot,
        logs=normalize_logs(getattr(result, "logs", None)),
        error_message=_truncate_error_message(getattr(result, "error", None)),
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


#: 页面关键元素快照脚本：站点无关，只取「可交互元素」的 tag / 稳定选择器 / 文本摘要，
#: 用于失败时回答「此刻页面上到底有没有这个按钮 / 是不是弹了验证码」这类问题。
#: 选择器优先 id，其次 data-* / class 组合，均无则回落到 tag + 同级序号。
_PAGE_SNAPSHOT_JS = """
() => {
  const textOf = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '')
    .replace(/\\s+/g, ' ').trim().slice(0, 80);
  const selectorOf = (el) => {
    if (el.id) return '#' + el.id;
    for (const attr of ['data-testid', 'data-e2e', 'name', 'aria-label', 'title']) {
      const v = el.getAttribute && el.getAttribute(attr);
      if (v) return el.tagName.toLowerCase() + '[' + attr + '="' + v.slice(0, 40) + '"]';
    }
    const cls = (el.className || '').toString().trim().split(/\\s+/).filter(Boolean).slice(0, 3);
    if (cls.length) return el.tagName.toLowerCase() + '.' + cls.join('.');
    return el.tagName.toLowerCase();
  };
  const nodes = Array.from(document.querySelectorAll(
    'button, a, input, textarea, select, [role="button"], [contenteditable=""], [contenteditable="true"]'
  )).slice(0, 60);
  return {
    url: location.href,
    title: document.title,
    elements: nodes.map((el) => ({
      tag: el.tagName.toLowerCase(),
      selector: selectorOf(el),
      text: textOf(el),
      visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
    })),
  };
}
"""


async def _capture_page_snapshot(page: Any, max_length: int) -> Optional[Dict]:
    """采集报错时的页面关键元素快照（站点无关）。

    Args:
        page: 当前执行页面；None / 无 ``evaluate_handle`` 时直接返回 None。
        max_length: 快照序列化后的字符预算（0 表示不限制）。

    Returns:
        含 url / title / elements 的 dict；任何异常（页面已关闭、JS 报错、
        返回值不是 dict）都返回 None —— 采集失败绝不影响业务执行。
    """
    if page is None:
        return None
    evaluate = getattr(page, "evaluate_handle", None)
    if not callable(evaluate):
        return None
    try:
        handle = await evaluate(_PAGE_SNAPSHOT_JS)
        value = await handle.json_value() if handle is not None else None
    except Exception:  # noqa: BLE001 - 快照是辅助信息，失败只降级为不记录
        return None
    if not isinstance(value, dict):
        return None
    return _truncate_payload(value, max_length)


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


# 截断标记：字符串被裁剪 / 列表项被裁剪时追加
_TRUNCATED_MARK = "…(已截断)"


def _dumps(value: Any) -> str:
    """序列化为 JSON 字符串（失败则退回 str），仅用于估算体积。"""
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _truncate_payload(data: Dict | None, max_length: int) -> Dict | None:
    """按 max_payload_length 压缩已序列化的 payload（0 表示不限制）。

    未超阈值原样返回；超阈值时**保持 JSON 结构**递归裁剪：dict 的键全部保留
    （预算按剩余键数均分），字符串超预算则截断、列表超预算只留前缀并追加统计。
    这样既压掉整包接口响应（如抽奖列表 items 及其 raw），又不丢 status_code /
    url / elapsed / is_json 等可读字段；整体裁剪后在顶层补 `_truncated` 标记。
    """
    if data is None or not max_length or max_length <= 0:
        return data
    serialized = _dumps(data)
    if len(serialized) <= max_length:
        return data
    shrunk = _shrink_value(data, max_length)
    if not isinstance(shrunk, dict):
        shrunk = {"value": shrunk}
    shrunk["_truncated"] = True
    shrunk["_original_length"] = len(serialized)
    return shrunk


def _shrink_value(value: Any, budget: int) -> Any:
    """在 budget 字符预算内返回 value 的结构近似（深度优先、就地裁剪）。"""
    if isinstance(value, str):
        if len(value) <= budget:
            return value
        return value[: max(budget, 0)] + _TRUNCATED_MARK
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        keys = list(value.keys())
        remaining = len(keys)
        for key in keys:
            share = max(budget // remaining, 0) if remaining else 0
            remaining -= 1
            item = _shrink_value(value[key], share)
            out[key] = item
            budget = max(budget - len(_dumps(item)), 0)
        return out
    if isinstance(value, (list, tuple)):
        out_list: list[Any] = []
        total = len(value)
        for item in value:
            if budget <= 0:
                out_list.append({"_truncated_items": total - len(out_list)})
                break
            shrunk_item = _shrink_value(item, budget)
            out_list.append(shrunk_item)
            budget = max(budget - len(_dumps(shrunk_item)), 0)
        return out_list
    # None / bool / int / float 等标量体积可忽略，原样保留
    return value


def normalize_logs(value: Any) -> list[str]:
    """将执行过程日志规范为字符串列表。

    `logs` 列的模型类型是 List[str]，但历史上曾误用 `_safe_jsonify` 写入，
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
