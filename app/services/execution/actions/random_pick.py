"""随机取值 Action（通用工具）

封装 random.choice：从给定列表中等概率随机选取一个元素。
不访问页面、不访问数据库，可在任意执行上下文使用。

列表来源：
    - items_var 指向变量池中的列表（点路径，如 comment_pool / last_output.data.list）；
    - 或直接给常量 items（元素支持 {{var}} 模板替换）。
    items_var 优先；两者都为空则动作失败。
"""

import random
import re
import time
from typing import Any, Dict, List

from app.models.execution.action_params import (
    BuiltinActionType,
    RandomPickParams,
    RandomPickResult,
)
from app.services.execution.actions.base import BaseAction, ActionResult

_TEMPLATE_RE = re.compile(r"\{\{([\w.]+)\}\}")


class RandomPickAction(BaseAction[RandomPickParams]):
    """从列表中随机选取一个元素"""

    action_id: BuiltinActionType = BuiltinActionType.RANDOM_PICK
    action_type: BuiltinActionType = BuiltinActionType.RANDOM_PICK
    params: RandomPickParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: RandomPickParams | None = None,
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

    def _walk(self, path: str) -> Any:
        cur: Any = self.variables
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return None
        return cur

    def _resolve_templates(self, value: str) -> str:
        if "{{" not in value:
            return value

        def _sub(m: "re.Match") -> str:
            v = self._walk(m.group(1))
            return "" if v is None else str(v)

        return _TEMPLATE_RE.sub(_sub, value)

    async def _execute(self) -> ActionResult[RandomPickResult]:
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

        # 1) 解析列表来源：items_var 优先，其次常量 items
        items: List[Any] | None = None
        if p.items_var:
            got = self._walk(p.items_var)
            if isinstance(got, list):
                items = got
            # 指向的不是列表时不静默回退常量，直接报错，便于发现配置问题
            if items is None and got is None and not p.items:
                return ActionResult(
                    success=False,
                    error=f"items_var 指向的变量不存在或不是列表: {p.items_var}",
                    execution_time=time.time() - start,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )
        if not items:
            items = [self._resolve_templates(str(x)) for x in (p.items or [])]

        if not items:
            return ActionResult(
                success=False,
                error="随机取值失败：items_var 与 items 均未提供非空列表",
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        # 2) 随机选取（可选种子，便于复现/测试）
        rng = random.Random(p.seed) if p.seed is not None else random
        idx = rng.randrange(len(items))
        chosen = items[idx]

        data = RandomPickResult(
            value="" if chosen is None else str(chosen),
            index=idx,
            total=len(items),
        )
        return ActionResult(
            success=True,
            data=data,
            execution_time=time.time() - start,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )
