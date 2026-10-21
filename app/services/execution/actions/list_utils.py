"""列表工具类 Action - 打乱 / 排序

对变量池中的某个列表做原地处理，并写回原路径，供后续 loop 步骤消费：

- ShuffleListAction 随机打乱列表（用于「预约 / 第三方抽奖」随机顺序执行）；
- SortListAction    按指定字段排序（用于「官方转发」按动态热度从大到小执行）。

两者都不访问页面/数据库；处理后的列表原地写回 items_var 指向的路径。
"""

import random
import time
from typing import Any, Dict, List

from loguru import logger

from app.models.execution.action_params import (
    BuiltinActionType,
    ShuffleListParams,
    ShuffleListResult,
    SortListParams,
    SortListResult,
    PickLineParams,
    PickLineResult,
)
from app.services.execution.actions.base import ActionResult, BaseAction, register_action_class


def _walk(obj: Any, path: str) -> Any:
    """按点路径取值，支持 dict 键、list 数字下标、对象属性。"""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            if not part.isdigit() or int(part) >= len(cur):
                return None
            cur = cur[int(part)]
        elif hasattr(cur, part):
            cur = getattr(cur, part)
        else:
            return None
    return cur


def _set(obj: Any, path: str, value: Any) -> bool:
    """按点路径写回值（就地修改父容器）。成功返回 True。"""
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if isinstance(cur, dict):
            if part not in cur:
                return False
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            if not part.isdigit() or int(part) >= len(cur):
                return False
            cur = cur[int(part)]
        elif hasattr(cur, part):
            cur = getattr(cur, part)
        else:
            return False

    last = parts[-1]
    if isinstance(cur, dict):
        cur[last] = value
        return True
    if isinstance(cur, list) and last.isdigit() and int(last) < len(cur):
        cur[int(last)] = value
        return True
    if hasattr(cur, last):
        setattr(cur, last, value)
        return True
    return False


class _ListActionMixin:
    """列表类动作公用逻辑：解析/写回列表，且不改写 last_output。"""

    def _resolve_list(self, items_var: str) -> List[Any] | None:
        got = _walk(self.variables, items_var)
        return got if isinstance(got, list) else None

    def _merge_output_vars(self, action_result: ActionResult) -> None:
        """仅按 output_vars 映射结果字段，不改写 last_output。"""
        data = action_result.data
        if data is None:
            return
        if isinstance(data, dict):
            data_dict = data
        elif hasattr(data, "model_dump"):
            data_dict = data.model_dump()
        else:
            return
        if self.output_vars:
            data_values = list(data_dict.values())
            for i, var_name in enumerate(self.output_vars):
                if i < len(data_values):
                    self.variables[var_name] = data_values[i]


class ShuffleListAction(_ListActionMixin, BaseAction[ShuffleListParams]):
    """随机打乱列表"""

    action_id: BuiltinActionType = BuiltinActionType.SHUFFLE_LIST
    action_type: BuiltinActionType = BuiltinActionType.SHUFFLE_LIST
    params: ShuffleListParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: ShuffleListParams | None = None,
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

    async def _execute(self) -> ActionResult[ShuffleListResult]:
        start = time.time()
        valid, error_msg, p = self.validate_params_with_model(self.params)
        if not valid or not p:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        items = self._resolve_list(p.items_var)
        if items is None:
            return ActionResult(
                success=False,
                error=f"items_var 指向的变量不存在或不是列表: {p.items_var}",
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        rng = random.Random(p.seed) if p.seed is not None else random
        shuffled = list(items)
        rng.shuffle(shuffled)
        _set(self.variables, p.items_var, shuffled)
        logger.info(f"[ShuffleListAction] {p.items_var} 打乱 {len(shuffled)} 项")

        return ActionResult(
            success=True,
            data=ShuffleListResult(total=len(shuffled)),
            execution_time=time.time() - start,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )


class SortListAction(_ListActionMixin, BaseAction[SortListParams]):
    """按字段对列表排序"""

    action_id: BuiltinActionType = BuiltinActionType.SORT_LIST
    action_type: BuiltinActionType = BuiltinActionType.SORT_LIST
    params: SortListParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: SortListParams | None = None,
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

    def _sort_key(self, item: Any) -> Any:
        p = self.params
        raw = _walk(item, p.field) if p.field else item
        if p.numeric:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return 0.0
        return "" if raw is None else str(raw)

    async def _execute(self) -> ActionResult[SortListResult]:
        start = time.time()
        valid, error_msg, p = self.validate_params_with_model(self.params)
        if not valid or not p:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        items = self._resolve_list(p.items_var)
        if items is None:
            return ActionResult(
                success=False,
                error=f"items_var 指向的变量不存在或不是列表: {p.items_var}",
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        try:
            ordered = sorted(items, key=self._sort_key, reverse=(p.order == "desc"))
        except Exception as e:  # noqa: BLE001
            return ActionResult(
                success=False,
                error=f"排序失败: {e}",
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        _set(self.variables, p.items_var, ordered)
        logger.info(
            f"[SortListAction] {p.items_var} 按 {p.field or '(自身)'} {p.order} 排序 {len(ordered)} 项"
        )
        return ActionResult(
            success=True,
            data=SortListResult(total=len(ordered)),
            execution_time=time.time() - start,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )


class PickLineAction(BaseAction[PickLineParams]):
    """按换行切分一段文本、过滤空行后随机取一条，写入 output_var"""

    action_id: BuiltinActionType = BuiltinActionType.PICK_LINE
    action_type: BuiltinActionType = BuiltinActionType.PICK_LINE
    params: PickLineParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: PickLineParams | None = None,
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

    async def _execute(self) -> ActionResult[PickLineResult]:
        start = time.time()
        valid, error_msg, p = self.validate_params_with_model(self.params)
        if not valid or not p:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        text = _walk(self.variables, p.text_var) if p.text_var else None
        if not isinstance(text, str):
            return ActionResult(
                success=False,
                error=f"text_var 指向的变量不存在或不是字符串: {p.text_var}",
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return ActionResult(
                success=False,
                error=f"text_var 文本按行切分后为空: {p.text_var}",
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        rng = random.Random(p.seed) if p.seed is not None else random
        chosen = rng.choice(lines)
        self.variables[p.output_var] = chosen
        logger.info(f"[PickLineAction] {p.text_var} 取到 1 条(共 {len(lines)} 行)")
        return ActionResult(
            success=True,
            data=PickLineResult(value=chosen, total=len(lines)),
            execution_time=time.time() - start,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )

# 自注册进内置动作注册表（base 的依赖倒置槽位，供控制流策略层按 action_id 查找）

register_action_class(ShuffleListAction)
register_action_class(SortListAction)
register_action_class(PickLineAction)
