"""
Base Action - 操作基类 (OOP 设计)

核心设计理念：
1. 单层验证：只验证当前 action 的参数，不递归验证子 action
2. DP 执行：使用动态规划算法展开操作链
3. 执行追踪：记录每个操作和插件的执行记录
4. 统一入口：action 和 plugin 在执行时统一判断

OOP 设计要点：
- 每个 Action 都是一个可执行单元
- Action 可以组合成 CompositeAction
- Action 可以作为 Plugin 挂载到其他 Action 的生命周期
"""

from abc import ABC, abstractmethod
from typing import Any, Type, Optional, List, Dict, Callable
from dataclasses import dataclass, field
from enum import Enum

from sqlmodel import SQLModel
from loguru import logger

from app.models.database.workflow.unified_models import (
    ActionParameter,
    ActionMetadata,
    ActionResult,
    ActionContext,
    ActionCategory,
)


class ExecutionPhase(Enum):
    """执行阶段"""
    VALIDATION = "validation"
    PRE_EXECUTION = "pre_execution"
    EXECUTION = "execution"
    POST_EXECUTION = "post_execution"
    CLEANUP = "cleanup"


@dataclass
class ExecutionNode:
    """执行节点 - 用于 DP 算法"""
    action_id: str
    params: Dict[str, Any]
    depth: int = 0
    parent_id: Optional[str] = None
    children: List['ExecutionNode'] = field(default_factory=list)
    is_expanded: bool = False
    metadata: Optional[ActionMetadata] = None


@dataclass
class ExecutionTrace:
    """执行追踪"""
    execution_id: str
    root_action_id: str
    nodes: Dict[str, ExecutionNode] = field(default_factory=dict)
    execution_order: List[str] = field(default_factory=list)
    completed: Dict[str, ActionResult] = field(default_factory=dict)


class BaseAction(ABC):
    """
    操作基类

    设计原则：
    1. validate() 只验证当前层参数，不验证子 action
    2. execute() 使用 DP 算法展开执行
    3. 每个子类只需实现 _do_execute() 方法
    """

    params_model: Type[SQLModel] | None = None

    def __init__(self, action_id: str | None = None):
        self.action_id = action_id or self.get_action_id()
        self.metadata = self.get_metadata()
        self._logs: List[str] = []
        self._execution_phase: ExecutionPhase = ExecutionPhase.VALIDATION

    @staticmethod
    @abstractmethod
    def get_action_id() -> str:
        """返回操作ID（静态方法，子类必须实现）"""
        ...

    @staticmethod
    def get_action_category() -> ActionCategory:
        """返回动作类别"""
        return ActionCategory.ATOMIC

    @classmethod
    def get_name(cls) -> str:
        """返回操作名称"""
        return cls.__name__.replace("Action", "")

    @abstractmethod
    async def _do_execute(self, ctx: ActionContext) -> ActionResult:
        """
        实际执行逻辑（子类必须实现）

        Args:
            ctx: 执行上下文

        Returns:
            ActionResult: 执行结果
        """
        ...

    def get_metadata(self) -> ActionMetadata:
        """返回操作元数据"""
        return ActionMetadata(
            id=self.action_id,
            name=self.get_name(),
            category=self.get_action_category(),
            description=self.__doc__ or "",
            parameters=self.get_parameters(),
            json_schema=self.get_json_schema(),
            timeout=30000,
            requires_browser=True,
        )

    def get_parameters(self) -> List[ActionParameter]:
        """获取参数定义"""
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

    def get_json_schema(self) -> dict[str, Any] | None:
        """获取完整 JSON Schema"""
        return None if not self.params_model else self.params_model.model_json_schema()

    def validate_params(self, params: dict[str, Any]) -> tuple[bool, str | None]:
        """
        验证参数（单层验证）

        只验证当前 action 的参数，不验证子 action。
        这是 OOP 设计的关键：每个 action 只负责自己的参数。

        Args:
            params: 待验证的参数

        Returns:
            (是否有效, 错误信息)
        """
        if not self.params_model:
            return True, None

        try:
            self.params_model(**params)
            return True, None
        except Exception as e:
            error_msg = f"参数验证失败: {str(e)}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}")
            return False, error_msg

    def preview(self, params: dict[str, Any]) -> tuple[bool, str | None]:
        """
        预览 action（单层预览）

        只预览当前 action，不预览子 action。
        用于 UI 展示参数校验结果。

        Args:
            params: 参数

        Returns:
            (是否有效, 错误信息)
        """
        return self.validate_params(params)

    def add_log(self, message: str):
        """添加执行日志"""
        self._logs.append(f"[{self._execution_phase.value}] {message}")

    def get_logs(self) -> List[str]:
        """获取执行日志"""
        return self._logs.copy()

    def clear_logs(self):
        """清空日志"""
        self._logs.clear()

    async def execute(self, ctx: ActionContext) -> ActionResult:
        """
        执行 action（使用 DP 算法）

        执行流程：
        1. 参数验证（单层）
        2. 前置处理
        3. 调用 _do_execute()
        4. 后置处理
        5. 返回结果

        Args:
            ctx: 执行上下文

        Returns:
            ActionResult: 执行结果
        """
        import time
        start_time = time.time()

        self.clear_logs()
        self._execution_phase = ExecutionPhase.VALIDATION

        # 1. 单层参数验证
        valid, error_msg = self.validate_params(ctx.params)
        if not valid:
            self.add_log(f"参数验证失败: {error_msg}")
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.action_id,
                action_name=self.metadata.name,
                logs=self.get_logs(),
            )

        self.add_log("参数验证通过")

        # 2. 前置处理
        self._execution_phase = ExecutionPhase.PRE_EXECUTION
        await self._pre_execute(ctx)

        # 3. 执行
        self._execution_phase = ExecutionPhase.EXECUTION
        result = await self._do_execute(ctx)
        result.execution_time = time.time() - start_time
        result.action_id = self.action_id
        result.action_name = self.metadata.name
        result.logs = self.get_logs()

        # 4. 后置处理
        self._execution_phase = ExecutionPhase.POST_EXECUTION
        await self._post_execute(ctx, result)

        self._execution_phase = ExecutionPhase.CLEANUP

        return result

    async def _pre_execute(self, ctx: ActionContext):
        """前置处理（可被子类重写）"""
        self.add_log(f"开始执行 action: {self.action_id}")

    async def _post_execute(self, ctx: ActionContext, result: ActionResult):
        """后置处理（可被子类重写）"""
        if result.success:
            self.add_log(f"action 执行成功，耗时: {result.execution_time:.2f}s")
        else:
            self.add_log(f"action 执行失败: {result.error}")


class CompositeAction(BaseAction):
    """
    组合动作基类

    组合多个原子动作或子组合动作。
    执行时使用 DP 算法展开操作链。
    """

    def __init__(self, action_id: str, name: str, description: str = "",
                 steps: List[Dict[str, Any]] | None = None):
        super().__init__(action_id)
        self._name = name
        self._description = description
        self._steps = steps or []
        self._registry: Optional['ActionRegistry'] = None

    @staticmethod
    def get_action_id() -> str:
        return "composite"

    @staticmethod
    def get_action_category() -> ActionCategory:
        return ActionCategory.COMPOSITE

    @classmethod
    def get_name(cls) -> str:
        return cls.__name__

    def set_registry(self, registry: 'ActionRegistry'):
        """设置注册表引用"""
        self._registry = registry

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id=self.action_id,
            name=self._name,
            category=ActionCategory.COMPOSITE,
            description=self._description,
            parameters=[],
            json_schema=None,
            timeout=30000,
            requires_browser=True,
        )

    def get_steps(self) -> List[Dict[str, Any]]:
        """获取步骤列表"""
        return self._steps

    def validate_params(self, params: dict[str, Any]) -> tuple[bool, str | None]:
        """
        组合动作参数验证

        注意：只验证组合动作自身的参数（如循环次数等），
        不验证子 action 的参数。子 action 的参数验证
        在执行时由各自的 action 完成。
        """
        return True, None

    async def _do_execute(self, ctx: ActionContext) -> ActionResult:
        """
        组合动作执行（使用 DP 算法）

        DP 算法设计：
        1. 构建执行图：根据 steps 构建依赖图
        2. 拓扑排序：确定执行顺序
        3. 动态展开：按顺序执行每个节点
        4. 结果传递：将结果传递给下游节点

        Args:
            ctx: 执行上下文

        Returns:
            ActionResult: 执行结果
        """
        from app.services.execution.unified_engine import unified_execution_engine

        self.add_log(f"开始执行组合动作，包含 {len(self._steps)} 个步骤")

        # 使用统一执行引擎执行
        results = await unified_execution_engine.execute_composite(
            steps=self._steps,
            ctx=ctx,
            registry=self._registry,
        )

        # 汇总结果
        total = len(results)
        success_count = sum(1 for r in results if r.success)
        failed_count = total - success_count
        total_time = sum(r.execution_time for r in results)

        self.add_log(f"组合动作执行完成: 成功 {success_count}/{total}")

        # 返回最后一个结果或失败信息
        last_result = results[-1] if results else None
        return ActionResult(
            success=last_result.success if last_result else False,
            data={
                "total_steps": total,
                "success_count": success_count,
                "failed_count": failed_count,
                "results": [
                    {
                        "action_id": r.action_id,
                        "action_name": r.action_name,
                        "success": r.success,
                        "error": r.error,
                        "execution_time": r.execution_time,
                    }
                    for r in results
                ]
            },
            error=last_result.error if last_result and not last_result.success else None,
            execution_time=total_time,
            action_id=self.action_id,
            action_name=self._name,
            logs=self.get_logs(),
        )


class PluginAction(BaseAction):
    """
    插件动作基类

    作为钩子挂载到其他 action 的生命周期。
    """

    def __init__(self, action_id: str, name: str, hook_type: str,
                 description: str = "", steps: List[Dict[str, Any]] | None = None):
        super().__init__(action_id)
        self._name = name
        self._hook_type = hook_type
        self._description = description
        self._steps = steps or []
        self._registry: Optional['ActionRegistry'] = None

    @staticmethod
    def get_action_id() -> str:
        return "plugin"

    @staticmethod
    def get_action_category() -> ActionCategory:
        return ActionCategory.PLUGIN

    def set_registry(self, registry: 'ActionRegistry'):
        self._registry = registry

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id=self.action_id,
            name=self._name,
            category=ActionCategory.PLUGIN,
            description=self._description,
            parameters=[],
            json_schema=None,
            timeout=30000,
            requires_browser=True,
        )

    def get_hook_type(self) -> str:
        return self._hook_type

    async def _do_execute(self, ctx: ActionContext) -> ActionResult:
        """插件执行"""
        from app.services.execution.unified_engine import unified_execution_engine

        self.add_log(f"插件 '{self._name}' 执行 (hook: {self._hook_type})")

        results = await unified_execution_engine.execute_composite(
            steps=self._steps,
            ctx=ctx,
            registry=self._registry,
        )

        last_result = results[-1] if results else None
        return ActionResult(
            success=last_result.success if last_result else False,
            data={"hook_type": self._hook_type, "results": results},
            error=last_result.error if last_result and not last_result.success else None,
            execution_time=sum(r.execution_time for r in results),
            action_id=self.action_id,
            action_name=self._name,
            logs=self.get_logs(),
        )
