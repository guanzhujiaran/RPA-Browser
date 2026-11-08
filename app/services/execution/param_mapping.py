"""循环参数映射注入（loop param_mapping → 子步骤 params）

语义（与循环实现保持一致，引擎不得擅自改写用户写死的配置）：

1. **只注入用户声明过该参数的步骤**：``param_mapping`` 的语义是「把某个参数的值
   换成循环项的字段」，不是给所有步骤凭空加参数；
2. **不改原对象**：总是返回副本，避免映射值残留在步骤定义里（下一轮映射为空时，
   上一轮的值会继续生效）；
3. **目标键支持点路径**：``"locator.value": "loop_item.link"`` 表示只替换
   已存在的 ``params["locator"]["value"]``，父级不存在时整条映射跳过。

点路径支持是为了配合定位器结构（``locator.value`` / ``locator.has_text``），
属于通用机制，不含任何站点语义。
"""

from __future__ import annotations

from typing import Any


def _set_nested(container: dict[str, Any], parts: list[str], value: Any) -> bool:
    """按点路径写入已存在的键；父级缺失或类型不符时返回 False（不新建结构）"""
    key = parts[0]
    if key not in container:
        return False
    if len(parts) == 1:
        container[key] = value
        return True
    child = container[key]
    if not isinstance(child, dict):
        return False
    child_copy = dict(child)
    if not _set_nested(child_copy, parts[1:], value):
        return False
    container[key] = child_copy
    return True


def apply_param_mapping(
    raw_params: dict[str, Any], mapped: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """把映射值注入到参数副本里

    Args:
        raw_params: 子步骤原始参数（dict）
        mapped: 目标键 → 映射值；键支持 ``a.b`` 形式的点路径

    Returns:
        (注入后的参数副本, 实际注入的键列表)
    """
    merged = {
        k: (dict(v) if isinstance(v, dict) else v) for k, v in (raw_params or {}).items()
    }
    applied: list[str] = []
    for target_key, value in (mapped or {}).items():
        if _set_nested(merged, str(target_key).split("."), value):
            applied.append(str(target_key))
    return merged, applied
