"""
CRUD 服务

拆分后的 CRUD 服务模块：
- action_crud: 自定义操作 CRUD
- plugin_crud: 插件 CRUD
- workflow_crud: 工作流 CRUD
- execution_log_crud: 执行日志 CRUD
- community_crud: 社区功能（点赞、举报）
"""
from app.services.execution.crud_service.action_crud import action_crud, ActionCrudService
from app.services.execution.crud_service.plugin_crud import plugin_crud, PluginCrudService
from app.services.execution.crud_service.workflow_crud import workflow_crud, WorkflowCrudService
from app.services.execution.crud_service.execution_log_crud import execution_log_crud, ExecutionLogCrudService
from app.services.execution.crud_service.community_crud import community_crud, CommunityCrudService

__all__ = [
    "action_crud",
    "ActionCrudService",
    "plugin_crud",
    "PluginCrudService",
    "workflow_crud",
    "WorkflowCrudService",
    "execution_log_crud",
    "ExecutionLogCrudService",
    "community_crud",
    "CommunityCrudService",
]
