"""
Scope — 变量作用域

数据结构：Scope 栈 (list[dict])，支持推入/弹出嵌套作用域。
算法：   Chain-of-responsibility 查找 — 从栈顶向栈底搜索键。

设计原则：
- 所有 Step 共享同一个 Scope 实例引用（单指针传递，无拷贝）
- push()/pop() 用于循环体/分支体的局部变量隔离
- resolve_params() 递归遍历参数树，替换 {{var}} 模板
"""

import re
from typing import Any


class Scope:
    """变量作用域栈。

    栈底是全局变量，栈顶是当前作用域。写入总是发生在栈顶，
    查找从栈顶向栈底遍历。

    用法:
        scope = Scope({"mid": 123})
        scope.set("up_name", "Alice")          # 写入栈顶
        scope.get("up_name")                   # "Alice"   (栈顶命中)
        scope.get("mid")                       # 123       (栈底命中)

        scope.push()                           # 推入新层（如进入循环体）
        scope.set("loop_index", 0)
        scope.pop()                            # 弹出（离开循环体）
    """

    __slots__ = ("_stack",)

    def __init__(self, initial: dict[str, Any] | None = None):
        self._stack: list[dict[str, Any]] = [dict(initial or {})]

    # ─── 基本操作 ───────────────────────────────────────

    @property
    def current(self) -> dict[str, Any]:
        """当前（栈顶）作用域 dict —— 可直接传给 BaseAction.variables（同一引用）。"""
        return self._stack[-1]

    def push(self) -> None:
        """推入新作用域层。"""
        self._stack.append({})

    def pop(self) -> None:
        """弹出当前作用域。至少保留一层（全局层不可弹出）。"""
        if len(self._stack) > 1:
            self._stack.pop()

    def depth(self) -> int:
        return len(self._stack)

    # ─── 读写 ───────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """从栈顶向栈底查找键。O(depth) 时间复杂度。"""
        for layer in reversed(self._stack):
            if key in layer:
                return layer[key]
        return default

    def set(self, key: str, value: Any) -> None:
        """写入当前（栈顶）作用域。"""
        self._stack[-1][key] = value

    def update(self, mapping: dict[str, Any]) -> None:
        """批量写入当前作用域。"""
        self._stack[-1].update(mapping)

    def __contains__(self, key: str) -> bool:
        for layer in reversed(self._stack):
            if key in layer:
                return True
        return False

    # ─── 模板替换 ───────────────────────────────────────

    _TEMPLATE_RE = re.compile(r"\{\{([\w.]+)\}\}")

    def resolve_text(self, text: str) -> str:
        """替换字符串中的 {{var}} 模板。"""
        if "{{" not in text:
            return text
        return self._TEMPLATE_RE.sub(
            lambda m: str(self.get(m.group(1), m.group(0))),
            text,
        )

    def resolve_params(self, params: Any) -> Any:
        """递归替换参数树中的模板变量。

        算法：深度优先遍历 dict/list/str 结构。
            - str: 正则替换 {{var}}
            - dict: 递归处理 values
            - list: 递归处理 elements
            - 其他: 原样返回

        时间复杂度：O(N)，N 为参数树中所有字符串总长度。
        """
        if params is None:
            return params
        if isinstance(params, str):
            return self.resolve_text(params)
        if isinstance(params, dict):
            return {k: self.resolve_params(v) for k, v in params.items()}
        if isinstance(params, list):
            return [self.resolve_params(v) for v in params]
        # Pydantic 模型
        if hasattr(params, "model_dump"):
            return self.resolve_params(params.model_dump())
        return params

    # ─── 快照 ───────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """展平所有作用域层为单一 dict（栈顶覆盖栈底）。"""
        result: dict[str, Any] = {}
        for layer in self._stack:
            result.update(layer)
        return result
