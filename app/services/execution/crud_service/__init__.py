"""
CRUD 服务

拆分后的 CRUD 服务模块：
- action_crud: 自定义操作 CRUD
- plugin_crud: 插件 CRUD
- workflow_crud: 工作流 CRUD
- execution_log_crud: 执行日志 CRUD
- community_crud: 社区功能（点赞、举报）
"""
from app.services.execution.crud_service.action_crud import action_crud_svr, ActionCrudService
from app.services.execution.crud_service.plugin_crud import plugin_crud_svr, PluginCrudService
from app.services.execution.crud_service.workflow_crud import workflow_crud_svr, WorkflowCrudService
from app.services.execution.crud_service.execution_log_crud import execution_log_crud_svr, ExecutionLogCrudService
from app.services.execution.crud_service.community_crud import community_crud_svr, CommunityCrudService

__all__ = [
    "action_crud_svr",
    "ActionCrudService",
    "plugin_crud_svr",
    "PluginCrudService",
    "workflow_crud_svr",
    "WorkflowCrudService",
    "execution_log_crud_svr",
    "ExecutionLogCrudService",
    "community_crud_svr",
    "CommunityCrudService",
]
