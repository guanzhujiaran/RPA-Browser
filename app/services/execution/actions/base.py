"""
Base Action - 操作基类
"""
from abc import ABC, abstractmethod
from typing import Any, Type
from sqlmodel import SQLModel
from loguru import logger

from app.models.core.workflow.models import (
    ActionParameter,
    ActionMetadata,
    ActionResult,
    ActionContext,
)
import typing
import types
from enum import Enum

class BaseAction(ABC):
    """操作基类"""

    params_model: Type[SQLModel] | None = None

    # 注意：以下常量已废弃，改用 SQLModel 验证
    # 保留仅用于参考

    def __init__(self):
        self.metadata = self.get_metadata()

    @abstractmethod
    def get_metadata(self) -> ActionMetadata:
        """返回操作元数据"""
        ...

    @abstractmethod
    async def execute(self, ctx: ActionContext) -> ActionResult:
        """执行操作"""
        ...

    def validate_params_with_model(self, params: dict[str, Any]) -> tuple[bool, str | None, SQLModel | None]:
        """使用 SQLModel 验证参数"""
        if not self.params_model:
            return True, None, None
        
        try:
            validated_params = self.params_model(**params)
            return True, None, validated_params
        except Exception as e:
            error_msg = f"参数验证失败: {str(e)}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}")
            return False, error_msg, None

    def validate_params(self, params: dict[str, Any]) -> tuple[bool, str | None]:
        """验证参数（兼容旧接口）"""
        valid, error_msg, _ = self.validate_params_with_model(params)
        return valid, error_msg
    
    def get_parameters_from_model(self) -> list[ActionParameter]:
        """从 params_model 自动提取参数元数据（直接返回原始 JSON Schema）"""
        if not self.params_model:
            return []
        
        parameters = []
        schema = self.params_model.model_json_schema()
        properties = schema.get('properties', {})
        
        for field_name in self.params_model.model_fields.keys():
            field_schema = properties.get(field_name, {})
            parameters.append(ActionParameter(
                name=field_name,
                json_schema=field_schema,
            ))
        
        return parameters
    
    def get_full_schema(self) -> dict[str, Any] | None:
        """获取完整的 JSON Schema（包含 $defs），用于前端解析 $ref 引用"""
        if not self.params_model:
            return None
        return self.params_model.model_json_schema()
