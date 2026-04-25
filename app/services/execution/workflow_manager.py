"""
工作流管理器

核心设计:
1. 管理用户自定义工作流
2. 工作流可以包含多个步骤
3. 支持工作流的导入导出
4. 工作流可以设置触发条件
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from enum import Enum


# 兼容 Python 3.10 的 StrEnum
class StrEnum(str, Enum):
    """字符串枚举，兼容 Python 3.10"""
    def __str__(self):
        return str(self.value)


import json
import uuid
import time
from loguru import logger

from app.services.execution.action_registry import Workflow, WorkflowStep


class WorkflowTriggerType(StrEnum):
    """工作流触发类型"""
    MANUAL = "manual"           # 手动触发
    SCHEDULED = "scheduled"     # 定时触发
    EVENT = "event"             # 事件触发
    API = "api"                 # API触发


@dataclass
class WorkflowMetadata:
    """工作流元数据"""
    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class WorkflowDefinition:
    """工作流定义"""
    metadata: WorkflowMetadata
    workflow: Workflow
    trigger_type: WorkflowTriggerType = WorkflowTriggerType.MANUAL
    trigger_config: Dict[str, Any] = field(default_factory=dict)
    is_enabled: bool = True


class WorkflowManager:
    """
    工作流管理器

    管理用户自定义工作流的创建、编辑、删除和执行
    """

    def __init__(self):
        self._workflows: Dict[str, WorkflowDefinition] = {}
        self._workflow_storage: Dict[str, Dict[str, Any]] = {}  # 用于持久化存储

    def create_workflow(
        self,
        name: str,
        steps: List[Dict[str, Any]],
        description: str = "",
        author: str = "",
        tags: Optional[List[str]] = None,
        on_error: str = "stop"
    ) -> WorkflowDefinition:
        """
        创建工作流

        Args:
            name: 工作流名称
            steps: 步骤列表 [{"action_id": "click", "params": {...}}, ...]
            description: 描述
            author: 作者
            tags: 标签
            on_error: 错误处理策略

        Returns:
            WorkflowDefinition: 工作流定义
        """
        workflow_id = str(uuid.uuid4())

        # 构建步骤
        workflow_steps = []
        for step_def in steps:
            step = WorkflowStep(
                action_id=step_def["action_id"],
                params=step_def.get("params", {}),
                retry=step_def.get("retry", 0),
                condition=None  # 暂不支持条件
            )
            workflow_steps.append(step)

        # 创建工作流
        workflow = Workflow(
            id=workflow_id,
            name=name,
            description=description,
            steps=workflow_steps,
            on_error=on_error
        )

        # 创建元数据
        metadata = WorkflowMetadata(
            id=workflow_id,
            name=name,
            description=description,
            author=author,
            tags=tags or []
        )

        # 创建完整定义
        definition = WorkflowDefinition(
            metadata=metadata,
            workflow=workflow
        )

        self._workflows[workflow_id] = definition
        logger.info(f"[WorkflowManager] 创建工作流: {name} (ID: {workflow_id})")

        return definition

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """获取工作流"""
        return self._workflows.get(workflow_id)

    def get_all_workflows(self) -> List[WorkflowMetadata]:
        """获取所有工作流元数据"""
        return [w.metadata for w in self._workflows.values()]

    def update_workflow(
        self,
        workflow_id: str,
        name: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        on_error: Optional[str] = None
    ) -> Optional[WorkflowDefinition]:
        """
        更新工作流

        Args:
            workflow_id: 工作流ID
            name: 新名称
            steps: 新步骤列表
            description: 新描述
            tags: 新标签
            on_error: 新错误处理策略

        Returns:
            更新后的工作流定义，失败返回None
        """
        definition = self._workflows.get(workflow_id)
        if not definition:
            return None

        if name:
            definition.metadata.name = name
            definition.workflow.name = name

        if description is not None:
            definition.metadata.description = description
            definition.workflow.description = description

        if tags is not None:
            definition.metadata.tags = tags

        if steps is not None:
            definition.workflow.steps = [
                WorkflowStep(
                    action_id=s["action_id"],
                    params=s.get("params", {}),
                    retry=s.get("retry", 0)
                )
                for s in steps
            ]

        if on_error:
            definition.workflow.on_error = on_error

        definition.metadata.updated_at = time.time()

        return definition

    def delete_workflow(self, workflow_id: str) -> bool:
        """删除工作流"""
        if workflow_id in self._workflows:
            del self._workflows[workflow_id]
            logger.info(f"[WorkflowManager] 删除工作流: {workflow_id}")
            return True
        return False

    def enable_workflow(self, workflow_id: str) -> bool:
        """启用工作流"""
        definition = self._workflows.get(workflow_id)
        if definition:
            definition.is_enabled = True
            return True
        return False

    def disable_workflow(self, workflow_id: str) -> bool:
        """禁用工作流"""
        definition = self._workflows.get(workflow_id)
        if definition:
            definition.is_enabled = False
            return True
        return False

    def export_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """
        导出工作流为JSON

        Args:
            workflow_id: 工作流ID

        Returns:
            工作流JSON定义
        """
        definition = self._workflows.get(workflow_id)
        if not definition:
            return None

        return {
            "metadata": {
                "id": definition.metadata.id,
                "name": definition.metadata.name,
                "version": definition.metadata.version,
                "description": definition.metadata.description,
                "author": definition.metadata.author,
                "tags": definition.metadata.tags,
                "created_at": definition.metadata.created_at,
                "updated_at": definition.metadata.updated_at,
            },
            "workflow": {
                "id": definition.workflow.id,
                "name": definition.workflow.name,
                "description": definition.workflow.description,
                "steps": [
                    {
                        "action_id": s.action_id,
                        "params": s.params,
                        "retry": s.retry
                    }
                    for s in definition.workflow.steps
                ],
                "on_error": definition.workflow.on_error
            },
            "trigger_type": definition.trigger_type,
            "trigger_config": definition.trigger_config,
            "is_enabled": definition.is_enabled
        }

    def import_workflow(self, workflow_json: Dict[str, Any]) -> Optional[WorkflowDefinition]:
        """
        从JSON导入工作流

        Args:
            workflow_json: 工作流JSON定义

        Returns:
            导入的工作流定义
        """
        try:
            metadata = WorkflowMetadata(
                id=workflow_json["metadata"]["id"],
                name=workflow_json["metadata"]["name"],
                version=workflow_json["metadata"].get("version", "1.0.0"),
                description=workflow_json["metadata"].get("description", ""),
                author=workflow_json["metadata"].get("author", ""),
                tags=workflow_json["metadata"].get("tags", []),
                created_at=workflow_json["metadata"].get("created_at", time.time()),
                updated_at=workflow_json["metadata"].get("updated_at", time.time())
            )

            workflow = Workflow(
                id=workflow_json["workflow"]["id"],
                name=workflow_json["workflow"]["name"],
                description=workflow_json["workflow"].get("description", ""),
                steps=[
                    WorkflowStep(
                        action_id=s["action_id"],
                        params=s.get("params", {}),
                        retry=s.get("retry", 0)
                    )
                    for s in workflow_json["workflow"]["steps"]
                ],
                on_error=workflow_json["workflow"].get("on_error", "stop")
            )

            definition = WorkflowDefinition(
                metadata=metadata,
                workflow=workflow,
                trigger_type=WorkflowTriggerType(workflow_json.get("trigger_type", "manual")),
                trigger_config=workflow_json.get("trigger_config", {}),
                is_enabled=workflow_json.get("is_enabled", True)
            )

            self._workflows[definition.workflow.id] = definition
            logger.info(f"[WorkflowManager] 导入工作流: {definition.metadata.name}")

            return definition

        except Exception as e:
            logger.error(f"[WorkflowManager] 导入工作流失败: {e}")
            return None

    def validate_workflow_steps(self, steps: List[Dict[str, Any]]) -> tuple[bool, Optional[str]]:
        """
        验证步骤列表

        Args:
            steps: 步骤列表

        Returns:
            (是否有效, 错误信息)
        """
        if not steps:
            return False, "步骤列表不能为空"

        for i, step in enumerate(steps):
            if "action_id" not in step:
                return False, f"步骤 {i+1} 缺少 action_id"

        return True, None


# 全局工作流管理器实例
workflow_manager = WorkflowManager()
