"""
Workflow Scheduler - 工作流调度器

使用 APScheduler 管理工作流的周期运行。

核心功能：
1. 基于 Crontab 表达式的周期调度
2. 工作流的增删改查
3. 调度任务的状态管理
"""

import uuid
from typing import Any, Dict, Optional
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.events import (
    EVENT_JOB_EXECUTED,
    EVENT_JOB_ERROR,
    EVENT_JOB_MISSED,
)
from loguru import logger
from sqlmodel import select

from app.models.database.workflow.unified_models import (
    WorkflowRecord,
    TriggerType,
    ExecutionStatus,
)
from app.services.execution.unified_engine import unified_execution_engine
from app.utils.depends.session_manager import DatabaseSessionManager


class WorkflowScheduler:
    """
    工作流调度器

    使用 APScheduler 管理工作流的周期运行。
    """

    def __init__(self):
        self._scheduler = AsyncIOScheduler(
            jobstores={
                "default": MemoryJobStore()
            },
            job_defaults={
                "coalesce": False,
                "max_instances": 1,
                "misfire_grace_time": 60 * 5,
            },
            timezone="UTC",
        )
        self._workflow_jobs: Dict[str, str] = {}
        self._setup_event_listeners()

    def _setup_event_listeners(self):
        """设置事件监听器"""
        self._scheduler.add_listener(
            self._on_job_executed,
            EVENT_JOB_EXECUTED
        )
        self._scheduler.add_listener(
            self._on_job_error,
            EVENT_JOB_ERROR
        )
        self._scheduler.add_listener(
            self._on_job_missed,
            EVENT_JOB_MISSED
        )

    def _on_job_executed(self, event):
        """任务执行完成"""
        logger.info(f"[Scheduler] 工作流 '{event.job_id}' 执行完成")

    def _on_job_error(self, event):
        """任务执行错误"""
        logger.error(f"[Scheduler] 工作流 '{event.job_id}' 执行失败: {event.exception}")

    def _on_job_missed(self, event):
        """任务错过执行"""
        logger.warning(f"[Scheduler] 工作流 '{event.job_id}' 错过执行")

    async def _execute_workflow_job(self, workflow_id: str, mid: int, params: Dict[str, Any]):
        """
        执行工作流任务

        Args:
            workflow_id: 工作流 ID
            mid: 用户 ID
            params: 触发参数
        """
        from app.services.RPA_browser.live_service import LiveService

        logger.info(f"[Scheduler] 开始执行工作流 '{workflow_id}' (mid={mid})")

        execution_id = str(uuid.uuid4())

        # 获取工作流
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRecord).where(WorkflowRecord.workflow_id == workflow_id)
            )
            workflow = result.first()

        if not workflow:
            logger.error(f"[Scheduler] 工作流 '{workflow_id}' 不存在")
            return

        if not workflow.is_enabled:
            logger.warning(f"[Scheduler] 工作流 '{workflow_id}' 已禁用")
            return

        # 获取浏览器会话
        session_key = LiveService._get_session_key(mid, int(params.get("browser_id", 0)))
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            logger.error(f"[Scheduler] 浏览器会话不存在: {session_key}")
            return

        try:
            page = await entry.plugined_session.get_current_page()
            browser = entry.plugined_session.browser_context

            # 构建执行上下文
            from app.models.database.workflow.unified_models import ActionContext

            ctx = ActionContext(
                session_id=str(params.get("browser_id", 0)),
                browser_id=str(params.get("browser_id", 0)),
                page=page,
                browser=browser,
                params=workflow.params_template,
                user_data={
                    "mid": str(mid),
                    "workflow_id": workflow_id,
                    "execution_id": execution_id,
                    "trigger_type": TriggerType.SCHEDULED,
                    "variables": {},
                },
            )

            # 从数据库加载 action
            from app.services.execution.action_registry import action_registry

            action = await action_registry.create_action_for_user(
                workflow.entry_action_id,
                str(mid),
            )

            if not action:
                logger.error(f"[Scheduler] 未找到 action: {workflow.entry_action_id}")
                return

            # 执行
            result = await unified_execution_engine._execute_with_plugins(
                action=action,
                ctx=ctx,
                mid=str(mid),
                execution_id=execution_id,
            )

            if result.success:
                logger.info(f"[Scheduler] 工作流 '{workflow_id}' 执行成功")
            else:
                logger.error(f"[Scheduler] 工作流 '{workflow_id}' 执行失败: {result.error}")

        except Exception as e:
            logger.error(f"[Scheduler] 执行工作流 '{workflow_id}' 失败: {e}")

    def start(self):
        """启动调度器"""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("[Scheduler] 工作流调度器已启动")

    def shutdown(self):
        """关闭调度器"""
        if self._scheduler.running:
            self._scheduler.shutdown()
            logger.info("[Scheduler] 工作流调度器已关闭")

    def add_workflow_schedule(
        self,
        workflow_id: str,
        mid: int,
        crontab_expression: str,
        params: Optional[Dict[str, Any]] = None,
    ):
        """
        添加工作流调度

        Args:
            workflow_id: 工作流 ID
            mid: 用户 ID
            crontab_expression: Crontab 表达式
            params: 触发参数

        Raises:
            ValueError: 无效的 Crontab 表达式
        """
        job_id = f"workflow_{workflow_id}"

        # 移除已有的调度
        self.remove_workflow_schedule(workflow_id)

        # 解析 Crontab 表达式
        try:
            cron_parts = crontab_expression.split()
            if len(cron_parts) != 5:
                raise ValueError("Crontab 表达式必须包含 5 个字段: 分 时 日 月 周")

            trigger = CronTrigger(
                minute=cron_parts[0],
                hour=cron_parts[1],
                day=cron_parts[2],
                month=cron_parts[3],
                day_of_week=cron_parts[4],
            )
        except ValueError as e:
            logger.error(f"[Scheduler] 无效的 Crontab 表达式: {crontab_expression} - {e}")
            raise

        # 添加任务
        job = self._scheduler.add_job(
            func=self._execute_workflow_job,
            trigger=trigger,
            id=job_id,
            args=[workflow_id, mid],
            kwargs={"params": params or {}},
            replace_existing=True,
        )

        self._workflow_jobs[workflow_id] = job_id
        logger.info(f"[Scheduler] 已添加工作流 '{workflow_id}' 的调度任务: {crontab_expression}")

        return job

    def remove_workflow_schedule(self, workflow_id: str) -> bool:
        """
        移除工作流调度

        Args:
            workflow_id: 工作流 ID

        Returns:
            bool: 是否成功移除
        """
        job_id = self._workflow_jobs.get(workflow_id)
        if not job_id:
            return False

        try:
            self._scheduler.remove_job(job_id)
            del self._workflow_jobs[workflow_id]
            logger.info(f"[Scheduler] 已移除工作流 '{workflow_id}' 的调度任务")
            return True
        except Exception as e:
            logger.error(f"[Scheduler] 移除调度失败: {e}")
            return False

    def pause_workflow_schedule(self, workflow_id: str) -> bool:
        """
        暂停工作流调度

        Args:
            workflow_id: 工作流 ID

        Returns:
            bool: 是否成功暂停
        """
        job_id = self._workflow_jobs.get(workflow_id)
        if not job_id:
            return False

        try:
            self._scheduler.pause_job(job_id)
            logger.info(f"[Scheduler] 已暂停工作流 '{workflow_id}' 的调度")
            return True
        except Exception as e:
            logger.error(f"[Scheduler] 暂停调度失败: {e}")
            return False

    def resume_workflow_schedule(self, workflow_id: str) -> bool:
        """
        恢复工作流调度

        Args:
            workflow_id: 工作流 ID

        Returns:
            bool: 是否成功恢复
        """
        job_id = self._workflow_jobs.get(workflow_id)
        if not job_id:
            return False

        try:
            self._scheduler.resume_job(job_id)
            logger.info(f"[Scheduler] 已恢复工作流 '{workflow_id}' 的调度")
            return True
        except Exception as e:
            logger.error(f"[Scheduler] 恢复调度失败: {e}")
            return False

    def get_schedule_status(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """
        获取调度状态

        Args:
            workflow_id: 工作流 ID

        Returns:
            调度状态信息
        """
        job_id = self._workflow_jobs.get(workflow_id)
        if not job_id:
            return None

        job = self._scheduler.get_job(job_id)
        if not job:
            return None

        return {
            "workflow_id": workflow_id,
            "job_id": job_id,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "is_paused": job.next_run_time is None,
        }

    def list_all_schedules(self) -> list[Dict[str, Any]]:
        """
        列出所有调度任务

        Returns:
            调度任务列表
        """
        jobs = self._scheduler.get_jobs()
        schedules = []

        for job in jobs:
            if job.id.startswith("workflow_"):
                workflow_id = job.id.replace("workflow_", "")
                schedules.append({
                    "workflow_id": workflow_id,
                    "job_id": job.id,
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                    "is_paused": job.next_run_time is None,
                })

        return schedules

    async def load_scheduled_workflows(self):
        """
        从数据库加载所有已启用的调度工作流

        在应用启动时调用，加载所有配置了 Crontab 的工作流。
        """
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(WorkflowRecord).where(
                    WorkflowRecord.is_scheduled == True,
                    WorkflowRecord.is_enabled == True,
                    WorkflowRecord.crontab_expression != None,
                )
            )
            workflows = result.all()

        for workflow in workflows:
            try:
                self.add_workflow_schedule(
                    workflow_id=workflow.workflow_id,
                    mid=workflow.mid,
                    crontab_expression=workflow.crontab_expression,
                    params={},
                )
                logger.info(f"[Scheduler] 已加载工作流调度: {workflow.name} ({workflow.crontab_expression})")
            except Exception as e:
                logger.error(f"[Scheduler] 加载工作流调度失败: {workflow.workflow_id} - {e}")

    def validate_crontab(self, expression: str) -> tuple[bool, Optional[str]]:
        """
        验证 Crontab 表达式

        Args:
            expression: Crontab 表达式

        Returns:
            (是否有效, 错误信息)
        """
        try:
            cron_parts = expression.split()
            if len(cron_parts) != 5:
                return False, "Crontab 表达式必须包含 5 个字段: 分 时 日 月 周"

            CronTrigger(
                minute=cron_parts[0],
                hour=cron_parts[1],
                day=cron_parts[2],
                month=cron_parts[3],
                day_of_week=cron_parts[4],
            )
            return True, None
        except Exception as e:
            return False, str(e)


workflow_scheduler = WorkflowScheduler()
