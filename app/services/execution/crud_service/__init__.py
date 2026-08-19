"""
CRUD 服务

拆分后的 CRUD 服务模块：
- action_crud: 自定义操作 CRUD
- plugin_crud: 插件 CRUD
- workflow_crud: 工作流 CRUD
- action_log_crud: 浏览器操作日志采集配置 + 日志记录 CRUD
- community_crud: 社区举报（点赞/收藏已迁移 be-message）
"""
from app.services.execution.crud_service.action_crud import action_crud_svr, ActionCrudService
from app.services.execution.crud_service.plugin_crud import plugin_crud_svr, PluginCrudService
from app.services.execution.crud_service.workflow_crud import workflow_crud_svr, WorkflowCrudService
from app.services.execution.crud_service.action_log_crud import (
    action_log_crud_svr,
    ActionLogCrudService,
)
from app.services.execution.crud_service.community_crud import community_crud_svr, CommunityCrudService

__all__ = [
    "action_crud_svr",
    "ActionCrudService",
    "plugin_crud_svr",
    "PluginCrudService",
    "workflow_crud_svr",
    "WorkflowCrudService",
    "action_log_crud_svr",
    "ActionLogCrudService",
    "community_crud_svr",
    "CommunityCrudService",
]
