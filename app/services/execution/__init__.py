"""
执行模块

核心设计:
1. 用户自定义操作系统
2. 操作注册表管理所有可用操作
3. 执行引擎负责执行操作和工作流
4. 工作流管理器管理用户工作流
5. CRUD 服务提供数据库持久化

主要组件:
- action_registry: 操作注册表，管理所有可执行操作
- execution_engine: 执行引擎，执行操作和工作流
- workflow_manager: 工作流管理器，管理用户工作流
- crud_service: CRUD 服务，提供数据库持久化

使用示例:

1. 注册自定义操作:
```python
from app.services.execution import action_registry

class MyCustomAction(BaseAction):
    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="my_action",
            name="我的自定义操作",
            type=ActionType.CUSTOM,
            parameters=[...]
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        # 实现操作逻辑
        ...

action_registry.register(MyCustomAction)
```

2. 执行操作:
```python
from app.services.execution import execution_engine

result = await execution_engine.execute_action(
    session_id="xxx",
    browser_id="xxx",
    page=page,
    browser=browser,
    action_id="click",
    params={"selector": "#button"}
)
```

3. 执行工作流:
```python
from app.services.execution import workflow_manager, execution_engine

# 创建工作流
workflow = workflow_manager.create_workflow(
    name="测试工作流",
    steps=[
        {"action_id": "navigate", "params": {"url": "https://example.com"}},
        {"action_id": "click", "params": {"selector": "#btn"}},
        {"action_id": "wait", "params": {"duration": 1000}},
    ]
)

# 执行工作流
results = await execution_engine.execute_workflow(
    session_id="xxx",
    browser_id="xxx",
    page=page,
    browser=browser,
    workflow=workflow.workflow
)
```

4. CRUD 操作:
```python
from app.services.execution import workflow_crud, action_crud

# 创建工作流
workflow = await workflow_crud.create(
    mid="123456",
    workflow_id="my_workflow",
    name="我的工作流",
    steps=[...],
)

# 查询工作流
workflows = await workflow_crud.list_by_user(mid="123456")

# 更新工作流
await workflow_crud.update(id=1, name="新名称")

# 删除工作流
await workflow_crud.delete(id=1)
```
"""

from app.services.execution.action_registry import (
    ActionRegistry,
    ActionMetadata,
    ActionParameter,
    ActionType,
    ActionContext,
    ActionResult,
    BaseAction,
    action_registry,
)
from app.services.execution.execution_engine import (
    ExecutionEngine,
    ExecutionStatus,
    ExecutionTask,
    WorkflowStep,
    Workflow,
    execution_engine,
)
from app.services.execution.crud_service import (
    ActionCrudService,
    WorkflowCrudService,
    ExecutionLogCrudService,
    action_crud,
    workflow_crud,
    execution_log_crud,
)

__all__ = [
    # Action Registry
    "ActionRegistry",
    "ActionMetadata",
    "ActionParameter",
    "ActionType",
    "ActionContext",
    "ActionResult",
    "BaseAction",
    "action_registry",
    # Execution Engine
    "ExecutionEngine",
    "ExecutionStatus",
    "ExecutionTask",
    "WorkflowStep",
    "Workflow",
    "execution_engine",
    # CRUD Services
    "ActionCrudService",
    "WorkflowCrudService",
    "ExecutionLogCrudService",
    "action_crud",
    "workflow_crud",
    "execution_log_crud",
]
