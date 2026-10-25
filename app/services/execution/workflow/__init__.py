"""工作流运行时包

原 ``workflow_runner`` 单文件（1094 行，含 408 行主执行体）按职责拆分为：

- ``precheck.py``   前置校验（目标/步骤/并发/时长）+ ``WorkflowRunnerError``
- ``notify.py``     运行通知（正文组装 + 外部推送/站内双通道）
- ``guards.py``     僵尸回收 / 会话释放 / 停止宽限期（收敛 3 处重复 RUNNING 查询）
- ``run_workflow.py`` 执行主体（阶段化：precheck → open_run → execute → finalize）
- ``launcher.py``   异步启动 / 任务收尾 / 断点恢复

公开 API（调用方零改动）经 ``workflow_runner`` 门面 re-export。
"""

from app.services.execution.workflow.guards import (
    _collect_zombie_runs,
    _list_running_for_browser,
    _reap_dead_run,
    _release_browser_after_run,
    _release_session_mark,
    enforce_stop_grace,
    list_alive_running_for_browser,
    reap_zombie_runs,
)
from app.services.execution.workflow.launcher import (
    _on_run_task_done,
    finalize_run_after_task,
    launch_workflow_run,
    requeue_pending_runs,
    resume_interrupted_runs,
    resume_run_record,
)
from app.services.execution.workflow.notify import _notify_run, _run_notify_content
from app.services.execution.workflow.precheck import (
    WorkflowRunnerError,
    _build_run_variables,
    _load_normalized_steps,
    _resolve_target_browser_id,
)
from app.services.execution.workflow.run_workflow import run_workflow

__all__ = [
    "WorkflowRunnerError",
    "_resolve_target_browser_id",
    "_load_normalized_steps",
    "_build_run_variables",
    "_notify_run",
    "_run_notify_content",
    "_list_running_for_browser",
    "list_alive_running_for_browser",
    "_release_browser_after_run",
    "_release_session_mark",
    "_reap_dead_run",
    "_collect_zombie_runs",
    "reap_zombie_runs",
    "enforce_stop_grace",
    "run_workflow",
    "launch_workflow_run",
    "requeue_pending_runs",
    "_on_run_task_done",
    "finalize_run_after_task",
    "resume_run_record",
    "resume_interrupted_runs",
]
