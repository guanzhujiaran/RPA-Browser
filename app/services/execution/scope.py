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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.execution.workflow_progress import WorkflowRunProgressReporter

# 缺失哨兵：区分「键不存在」与「键存在但值为 None」
_MISSING = object()


class TemplateResolveError(Exception):
    """模板变量解析失败（变量缺失、值为 None 或点路径中断）。

    按项目约定，动作执行时模板变量解析失败必须显式抛出并终止当前步骤，
    禁止将未解析的 ``{{placeholder}}`` 字面量或空值静默带入下游动作继续执行。
    """

    def __init__(self, placeholder: str, path: str, reason: str):
        self.placeholder = placeholder
        self.path = path
        self.reason = reason
        super().__init__(f"模板变量解析失败: {placeholder}（{reason}）")


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

    __slots__ = ("_stack", "cancel_check", "progress", "loop_nesting")

    def __init__(self, initial: dict[str, Any] | None = None):
        self._stack: list[dict[str, Any]] = [dict(initial or {})]
        # 协作式停止钩子：由执行引擎按 run_id 注入，步骤边界调用（见 cancellation.py）。
        # None 表示当前执行不参与取消（如单步调试 / 预览）。
        self.cancel_check: Callable[[], bool] | None = None
        # 运行进度上报器：仅工作流调度执行注入（见 workflow_progress.py）；
        # 单操作执行 / 调试为 None，埋点自动跳过。
        self.progress: "WorkflowRunProgressReporter | None" = None
        # 循环嵌套深度：Pipeline 的 LoopStep 没有 push/pop 作用域，
        # 用它给进度上报区分「最外层循环」（外层轮次才作为进度主轴）。
        self.loop_nesting: int = 0

    # ─── 基本操作 ───────────────────────────────────────

    def is_cancelled(self) -> bool:
        """是否已请求停止本次执行（无钩子时恒为 False）。"""
        return self.cancel_check is not None and self.cancel_check()

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

    def lookup_path(self, path: str) -> tuple[bool, Any]:
        """按点路径取值，支持嵌套对象/字典下钻。

        优先按整体键名查找（兼容以点号命名的扁平变量）；未命中时再按
        ``.`` 拆分逐级下钻，例如 ``off_item.dynId`` → 先取变量 ``off_item``，
        再从该 dict/对象取 ``dynId``。

        Returns:
            (found, value)。found=False 表示路径不存在。
        """
        # 1) 整体键查找（保持既有扁平键语义）
        flat = self.get(path, _MISSING)
        if flat is not _MISSING:
            return True, flat

        parts = path.split(".")
        if len(parts) == 1:
            return False, None
        current = self.get(parts[0], _MISSING)
        if current is _MISSING:
            return False, None
        for part in parts[1:]:
            if isinstance(current, dict):
                if part not in current:
                    return False, None
                current = current[part]
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return False, None
        return True, current

    def resolve_text(
        self,
        text: str,
        default: str | None = None,
        strict: bool = False,
    ) -> str:
        """替换字符串中的 {{var}} 模板。

        模板变量支持点路径（如 ``{{item.dynId}}``）。

        Args:
            default: 缺失或值为 None 时的默认返回值（strict=False 时生效）。None 表示保留
                     原行为（缺失变量保留 {{var}} 字面量，None 值转为 "None" 字符串）。
            strict: True 时，模板变量缺失 / 值为 None / 点路径中断直接抛
                    ``TemplateResolveError``，不再静默替换为字面量或空值。
        """
        if "{{" not in text:
            return text

        def _replace(m):
            found, val = self.lookup_path(m.group(1))
            if strict and (not found or val is None):
                reason = "变量不存在或点路径中断" if not found else "变量值为空"
                raise TemplateResolveError(m.group(0), m.group(1), reason)
            if default is not None:
                return default if (not found or val is None) else str(val)
            # 向后兼容：缺失变量保留字面量，None 值转为 "None" 字符串
            return m.group(0) if not found else str(val)

        return self._TEMPLATE_RE.sub(_replace, text)

    def resolve_params(
        self,
        params: Any,
        default: str | None = None,
        strict: bool = False,
    ) -> Any:
        """递归替换参数树中的模板变量。

        算法：深度优先遍历 dict/list/str 结构。
            - str: 正则替换 {{var}}
            - dict: 递归处理 values
            - list: 递归处理 elements
            - 其他: 原样返回

        Args:
            default: 缺失或值为 None 时的默认返回值（strict=False 时生效）。
            strict: True 时任一模板变量解析失败即抛 ``TemplateResolveError``。

        时间复杂度：O(N)，N 为参数树中所有字符串总长度。
        """
        if params is None:
            return params
        if isinstance(params, str):
            return self.resolve_text(params, default, strict)
        if isinstance(params, dict):
            return {
                k: self.resolve_params(v, default, strict) for k, v in params.items()
            }
        if isinstance(params, list):
            return [self.resolve_params(v, default, strict) for v in params]
        # Pydantic 模型
        if hasattr(params, "model_dump"):
            return self.resolve_params(params.model_dump(), default, strict)
        return params

    # ─── 快照 ───────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """展平所有作用域层为单一 dict（栈顶覆盖栈底）。"""
        result: dict[str, Any] = {}
        for layer in self._stack:
            result.update(layer)
        return result
