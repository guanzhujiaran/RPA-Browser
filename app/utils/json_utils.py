"""通用 JSON 序列化 / 裁剪工具

两个消费方共用一份口径，避免「日志里的结果」和「消息栏里的结果」长得不一样：

- `app/services/execution/action_logger.py`：操作日志落库（params / result / variables）；
- `app/services/execution/workflow_progress.py`：SSE 运行消息的 `detail`（步骤返回内容）。

设计要点：
- `to_jsonable`：把任意对象（pydantic 模型 / datetime / 集合 / 可调用对象）递归转为
  可 JSON 序列化结构，带深度上限，绝不抛异常；
- `shrink_json_payload`：超长时**保持 JSON 结构**递归裁剪（dict 的键全保留、字符串截断、
  列表只留前缀并追加统计），这样压掉大列表的同时不会丢掉 `status_code` / `url` 等可读字段；
- `truncate_text`：纯文本硬截断（结构裁剪之后的最后兜底）。
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime

#: 截断标记：字符串被裁剪 / 列表项被裁剪时追加
TRUNCATED_MARK = "…(已截断)"
#: 递归深度上限：超过后直接 str 化，避免循环引用打爆调用栈
_MAX_DEPTH = 8


def to_jsonable(value: object, depth: int = 0) -> object:
    """递归将任意对象转为可 JSON 序列化的结构。"""
    if depth > _MAX_DEPTH:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        try:
            return to_jsonable(value.model_dump(), depth + 1)
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v, depth + 1) for v in value]
    if callable(value):
        return f"<callable {getattr(value, '__name__', 'anonymous')}>"
    return str(value)


def dumps_json(value: object) -> str:
    """序列化为 JSON 字符串（失败则退回 str），仅用于估算体积。"""
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def shrink_value(value: object, budget: int) -> object:
    """在 budget 字符预算内返回 value 的结构近似（深度优先、就地裁剪）。"""
    if isinstance(value, str):
        if len(value) <= budget:
            return value
        return value[: max(budget, 0)] + TRUNCATED_MARK
    if isinstance(value, dict):
        out: dict[str, object] = {}
        keys = list(value.keys())
        remaining = len(keys)
        for key in keys:
            share = max(budget // remaining, 0) if remaining else 0
            remaining -= 1
            item = shrink_value(value[key], share)
            out[key] = item
            budget = max(budget - len(dumps_json(item)), 0)
        return out
    if isinstance(value, (list, tuple)):
        out_list: list[object] = []
        total = len(value)
        for item in value:
            if budget <= 0:
                out_list.append({"_truncated_items": total - len(out_list)})
                break
            shrunk_item = shrink_value(item, budget)
            out_list.append(shrunk_item)
            budget = max(budget - len(dumps_json(shrunk_item)), 0)
        return out_list
    # None / bool / int / float 等标量体积可忽略，原样保留
    return value


def shrink_json_payload(data: dict | None, max_length: int) -> dict | None:
    """按 max_length 压缩已序列化的 payload（0 表示不限制）。

    未超阈值原样返回；超阈值时**保持 JSON 结构**递归裁剪：dict 的键全部保留
    （预算按剩余键数均分），字符串超预算则截断、列表超预算只留前缀并追加统计。
    整体裁剪后在顶层补 `_truncated` 标记与原始长度。
    """
    if data is None or not max_length or max_length <= 0:
        return data
    serialized = dumps_json(data)
    if len(serialized) <= max_length:
        return data
    shrunk = shrink_value(data, max_length)
    if not isinstance(shrunk, dict):
        shrunk = {"value": shrunk}
    shrunk["_truncated"] = True
    shrunk["_original_length"] = len(serialized)
    return shrunk


def truncate_text(text: str, max_length: int) -> str:
    """纯文本硬截断（0 表示不限制）；超长追加截断标记。"""
    if not max_length or max_length <= 0 or len(text) <= max_length:
        return text
    return text[:max_length] + TRUNCATED_MARK


def jsonify(value: object) -> dict | None:
    """把任意值包成可存 JSON 列的 dict；None 原样返回，失败则返回错误信息 dict。"""
    if value is None:
        return None
    try:
        data = to_jsonable(value)
        if not isinstance(data, dict):
            data = {"value": data}
        return data
    except Exception:
        return {"_serialization_error": str(traceback.format_exc())}


__all__ = [
    "TRUNCATED_MARK",
    "dumps_json",
    "jsonify",
    "shrink_json_payload",
    "shrink_value",
    "to_jsonable",
    "truncate_text",
]
