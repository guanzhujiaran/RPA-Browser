"""复合动作「语义内容指纹」（content_hash）计算

用于把"两个动作内容是否等价"从时间戳比较改为内容相等性判断，
详见 ``docs/动作版本与内容指纹计划书.md`` §3。

口径要点：

- 只对**语义内容**取哈希：``steps`` / ``parameters_schema`` / ``input_vars`` / ``output_vars``；
- 展示、可见性与运行时策略（``name`` / ``description`` / ``icon_*`` / ``is_public`` /
  ``is_enabled`` / ``tags`` / ``timeout`` / ``retry_*`` / ``log_*``）一律不参与，
  避免"上游只换了个图标"就向所有 Fork 用户报"有新版本"；
- 序列化采用 ``sort_keys`` + 紧凑分隔符 + ``ensure_ascii=False``，
  保证同内容必得同串，且与 dict 插入顺序、中文转义无关。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

#: ``content_hash`` 的十六进制长度（sha256）
CONTENT_HASH_LENGTH = 64


@dataclass(slots=True, frozen=True)
class ActionSemanticContent:
    """参与内容指纹计算的动作字段（"语义内容"）

    由调用方从 ``CompositeActionModel`` 或请求体构造；字段集合必须与计划书 §3.1
    完全一致，新增/移除字段都要同步更新本文档与 ``_HASHED_FIELDS``。
    """

    steps: Sequence[object]
    parameters_schema: Sequence[object]
    input_vars: Sequence[object]
    output_vars: Sequence[str]


#: 参与哈希的字段顺序（仅用于文档与测试断言，实际由 dataclass 字段决定）
_HASHED_FIELDS: tuple[str, ...] = (
    "steps",
    "parameters_schema",
    "input_vars",
    "output_vars",
)


def _to_plain(value: object) -> object:
    """递归把 pydantic 模型 / 映射 / 元组归一为 JSON 原生结构

    归一化后 ``json.dumps`` 才能稳定序列化，避免同一份内容因对象类型不同
    （``BaseWorkflowStep`` 实例 vs ``dict``）算出不同哈希。
    """
    if isinstance(value, BaseModel):
        return _to_plain(value.model_dump(exclude_none=True, mode="json"))
    if isinstance(value, Mapping):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _to_plain(model_dump(exclude_none=True, mode="json"))
    return value


def compute_content_hash(content: ActionSemanticContent) -> str:
    """计算语义内容的 sha256 十六进制指纹（64 位小写）

    Args:
        content: 参与计算的四个字段

    Returns:
        64 位小写十六进制字符串；空内容同样返回确定值，不会返回空串
    """
    payload = {field: _to_plain(getattr(content, field)) for field in _HASHED_FIELDS}
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_content_hash_from_fields(
    *,
    steps: Sequence[object] | None,
    parameters_schema: Sequence[object] | None,
    input_vars: Sequence[object] | None,
    output_vars: Sequence[str] | None,
) -> str:
    """``compute_content_hash`` 的便捷入口（自动把 ``None`` 视为空列表）

    服务层写库前通常拿到的是可空字段，这里统一归一，避免调用方各写一遍 ``or []``。
    """
    return compute_content_hash(
        ActionSemanticContent(
            steps=steps or [],
            parameters_schema=parameters_schema or [],
            input_vars=input_vars or [],
            output_vars=output_vars or [],
        )
    )


def short_hash(content_hash: str) -> str:
    """取指纹短前缀（8 位），用于日志/通知等不便展示完整哈希的场景"""
    return content_hash[:8] if content_hash else ""
