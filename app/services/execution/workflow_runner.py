"""工作流执行器（调度外壳的运行时）—— 兼容入口

原 1094 行单文件已按职责拆分为 ``app/services/execution/workflow/`` 包：

===========================  ============================================
模块                         职责
===========================  ============================================
``workflow/precheck.py``     前置校验 + ``WorkflowRunnerError``
``workflow/notify.py``       运行通知（双通道投递）
``workflow/guards.py``       僵尸回收 / 会话释放 / 停止宽限期
``workflow/run_workflow.py`` 执行主体（阶段化 helper）
``workflow/launcher.py``     异步启动 / 任务收尾 / 断点恢复
===========================  ============================================

职责：把「工作流（引用一个复合操作 + 触发配置 + 目标浏览器）」真正跑起来，
并产出可观测结果：
    - WorkflowRunRecord：一次运行的聚合记录（不依赖 action 的 log_enabled）
    - ActionLogRecord：步骤级明细（由 ExecutionEngine.execute_steps 自动写入，
      source=workflow + workflow_id，通过 execution_id 与运行记录 1:1 关联）
    - 运行通知：开始/结束（成功/失败）双通道汇报，外部推送复用
      NotificationConfig + push_msg，站内系统通知经 be-message RPC（无需配置）

设计约束：
    1. 工作流不改写引用的 action，只按顺序执行其 steps（action 是多对一共享资产）。
    2. 定时运行允许自动拉起浏览器会话（headless），无需人工先开会话。
    3. 本模块所有异常都被收敛为「运行失败」，绝不向上抛出打断调度线程。

本文件仅做**全量 re-export**，保证
``from app.services.execution.workflow_runner import X`` 既有引用零改动；
新代码请直接从 ``app.services.execution.workflow`` 子模块导入。
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
    _load_normalized_steps,
    _resolve_target_browser_id,
)
from app.services.execution.workflow.run_workflow import run_workflow

__all__ = [
    "WorkflowRunnerError",
    "_resolve_target_browser_id",
    "_load_normalized_steps",
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
    "_on_run_task_done",
    "finalize_run_after_task",
    "resume_run_record",
    "resume_interrupted_runs",
    "requeue_pending_runs",
]
