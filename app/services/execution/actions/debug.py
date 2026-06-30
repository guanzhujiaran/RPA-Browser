"""
调试类 Action - Print
打印变量替换后的参数，不执行任何实际操作，用于调试
"""
import time
from typing import Dict, List

from app.models.execution.action_params import (
    BuiltinActionType, PrintParams, PrintResult,
)
from loguru import logger
from app.services.execution.actions.base import BaseAction, ActionResult


class PrintAction(BaseAction[PrintParams]):
    """打印参数操作（调试用）- 打印变量替换后的内容，不执行任何实际操作"""

    action_id: BuiltinActionType = BuiltinActionType.PRINT
    action_type: BuiltinActionType = BuiltinActionType.PRINT
    params: PrintParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict, params: PrintParams | None = None, timeout: int = 30000, input_vars: Dict | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            'action_id': cls.action_id,
            'action_type': cls.action_type,
            'mid': mid,
            'page': page,
            'params': safe_params,
            'timeout': timeout,
            'input_vars': input_vars or {},
            'output_vars': output_vars or [],
            'variables': variables or {},
        }
        if action_name is not None:
            kwargs['_action_name'] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[PrintResult]:
        """打印替换后的参数内容，不执行任何实际操作"""
        start_time = time.time()
        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
        if not valid or not validated_params:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
        message = validated_params.message
        logger.info(f"[PrintAction] {message}")
        return ActionResult(
            success=True, data=PrintResult(message=message),
            execution_time=time.time() - start_time,
            action_id=self.metadata.id, action_name=self.metadata.name,
        )
