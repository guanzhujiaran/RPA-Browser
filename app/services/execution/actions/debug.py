"""
调试类 Action - Print
打印变量替换后的参数，不执行任何实际操作，用于调试
"""

import json
import time
from typing import Any, Dict, List

from app.models.execution.action_params import (
    BuiltinActionType,
    PrintParams,
    PrintResult,
)
from loguru import logger
from app.services.execution.actions.base import ActionResult, BaseAction, register_action_class


class PrintAction(BaseAction[PrintParams]):
    """打印参数操作（调试用）- 打印变量替换后的内容，不执行任何实际操作"""

    action_id: BuiltinActionType = BuiltinActionType.PRINT
    action_type: BuiltinActionType = BuiltinActionType.PRINT
    params: PrintParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: PrintParams | None = None,
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

    async def _execute(self) -> ActionResult[PrintResult]:
        """打印替换后的参数内容，不执行任何实际操作"""
        start_time = time.time()
        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or not validated_params:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        message = validated_params.message
        dump_text = ""
        if validated_params.dump_variables:
            dump_text = self._format_variables(
                max_items=validated_params.dump_max_items,
                max_length=validated_params.dump_max_length,
            )
            message = f"{message}\n{dump_text}" if message else dump_text
        logger.info(f"[PrintAction] {message}")
        return ActionResult(
            success=True,
            data=PrintResult(message=message),
            logs=[dump_text] if dump_text else [],
            execution_time=time.time() - start_time,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )

    def _format_variables(self, *, max_items: int, max_length: int) -> str:
        """把当前变量池格式化成可读文本（调试用）。

        - 跳过可调用对象（如 execute_steps_func / cancel_check）与下划线开头的内部变量
        - 列表/字典只展开前 max_items 项，并标注总条数，避免刷爆日志
        - 单个变量超长按 max_length 截断
        """
        lines: list[str] = []
        items = [
            (name, value)
            for name, value in self.variables.items()
            if not str(name).startswith("_") and not callable(value)
        ]
        lines.append(f"===== 变量池快照：共 {len(items)} 个变量 =====")
        for name, value in items:
            lines.append(f"- {name} = {self._format_value(value, max_items, max_length)}")
        return "\n".join(lines)

    @staticmethod
    def _format_value(value: Any, max_items: int, max_length: int) -> str:
        try:
            if isinstance(value, (list, tuple)):
                total = len(value)
                head = list(value[:max_items]) if max_items else []
                body = json.dumps(head, ensure_ascii=False, default=str)
                suffix = " ..." if max_items and total > max_items else ""
                text = f"list[{total}] {body}{suffix}"
            elif isinstance(value, dict):
                # 取数结果常见形态 {"items": [...]}：单独标注列表条数，避免整段被截断后看不到总数
                inner = value.get("items")
                if isinstance(inner, list):
                    total_inner = len(inner)
                    head_items = inner[:max_items] if max_items else []
                    body = json.dumps(head_items, ensure_ascii=False, default=str)
                    suffix = " ..." if max_items and total_inner > max_items else ""
                    text = f"dict[{len(value)}] items=list[{total_inner}] {body}{suffix}"
                else:
                    total = len(value)
                    keys = list(value.keys())[:max_items] if max_items else []
                    head = {k: value[k] for k in keys}
                    body = json.dumps(head, ensure_ascii=False, default=str)
                    suffix = " ..." if max_items and total > max_items else ""
                    text = f"dict[{total}] {body}{suffix}"
            else:
                text = repr(value)
        except Exception:  # noqa: BLE001 - 序列化失败不影响主流程
            text = repr(value)
        if len(text) <= max_length:
            return text
        return f"{text[:max_length]}...(截断)"


# 自注册进内置动作注册表（base 的依赖倒置槽位，供控制流策略层按 action_id 查找）

register_action_class(PrintAction)
