"""全局后台任务调度器管理"""

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from typing import Callable, Optional
from functools import wraps


class SchedulerManager:
    """全局后台任务调度器管理器"""

    _instance: Optional["SchedulerManager"] = None
    _scheduler: Optional[AsyncIOScheduler] = None

    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化调度器"""
        if SchedulerManager._scheduler is None:
            SchedulerManager._scheduler = AsyncIOScheduler()
            logger.info("✅ SchedulerManager initialized")

    @property
    def scheduler(self) -> AsyncIOScheduler:
        """获取调度器实例"""
        if self._scheduler is None:
            raise RuntimeError("Scheduler not initialized")
        return self._scheduler

    def add_interval_job(
        self,
        func: Callable,
        seconds: Optional[int] = None,
        minutes: Optional[int] = None,
        hours: Optional[int] = None,
        id: Optional[str] = None,
        name: Optional[str] = None,
        replace_existing: bool = False,
        misfire_grace_time: Optional[int] = None,
        **kwargs,
    ):
        """添加定时任务（间隔触发）

        Args:
            func: 要执行的函数
            seconds: 间隔秒数
            minutes: 间隔分钟数
            hours: 间隔小时数
            id: 任务ID
            name: 任务名称
            replace_existing: 是否替换已存在的任务
            misfire_grace_time: 错过执行时间的宽限时间（秒），None 表示立即执行
            **kwargs: 传递给函数的其他参数
        """
        job_id = id or func.__name__

        # 构建触发器参数，只传递非 None 的参数
        trigger_kwargs = {}
        if seconds is not None:
            trigger_kwargs["seconds"] = seconds
        if minutes is not None:
            trigger_kwargs["minutes"] = minutes
        if hours is not None:
            trigger_kwargs["hours"] = hours

        self.scheduler.add_job(
            func,
            trigger=IntervalTrigger(**trigger_kwargs),
            id=job_id,
            name=name or job_id,
            replace_existing=replace_existing,
            misfire_grace_time=misfire_grace_time,
            **kwargs,
        )
        logger.info(
            f"✅ Added interval job: {job_id} (interval: {seconds}s/{minutes}m/{hours}h)"
        )

    def add_cron_job(
        self,
        func: Callable,
        cron_expression: str,
        id: Optional[str] = None,
        name: Optional[str] = None,
        replace_existing: bool = False,
        **kwargs,
    ):
        """添加定时任务（Cron 表达式触发）

        Args:
            func: 要执行的函数
            cron_expression: Cron 表达式，例如: "*/5 * * * *" (每5分钟)
            id: 任务ID
            name: 任务名称
            replace_existing: 是否替换已存在的任务
            **kwargs: 传递给函数的其他参数
        """
        job_id = id or func.__name__

        # 解析 cron 表达式
        cron_parts = cron_expression.split()
        if len(cron_parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expression}")

        minute, hour, day, month, day_of_week = cron_parts

        self.scheduler.add_job(
            func,
            trigger=CronTrigger(
                minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week
            ),
            id=job_id,
            name=name or job_id,
            replace_existing=replace_existing,
            **kwargs,
        )
        logger.info(f"✅ Added cron job: {job_id} (cron: {cron_expression})")

    def remove_job(self, job_id: str):
        """移除任务

        Args:
            job_id: 任务ID
        """
        self.scheduler.remove_job(job_id)
        logger.info(f"✅ Removed job: {job_id}")

    def pause_job(self, job_id: str):
        """暂停任务

        Args:
            job_id: 任务ID
        """
        self.scheduler.pause_job(job_id)
        logger.info(f"⏸️ Paused job: {job_id}")

    def resume_job(self, job_id: str):
        """恢复任务

        Args:
            job_id: 任务ID
        """
        self.scheduler.resume_job(job_id)
        logger.info(f"▶️ Resumed job: {job_id}")

    def get_jobs(self):
        """获取所有任务"""
        return self.scheduler.get_jobs()

    def start(self):
        """启动调度器"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("🚀 Scheduler started")

    def shutdown(self, wait: bool = True):
        """关闭调度器

        Args:
            wait: 是否等待任务完成
        """
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)
            logger.info("🛑 Scheduler shutdown")

    def async_wrapper(self, func: Callable) -> Callable:
        """将同步函数包装为异步函数

        Args:
            func: 要包装的函数

        Returns:
            异步函数
        """

        @wraps(func)
        async def async_func(*args, **kwargs):
            return await asyncio.to_thread(func, *args, **kwargs)

        return async_func


# 全局调度器实例
scheduler_manager = SchedulerManager()


# 装饰器：定时任务（间隔触发）
def interval_job(
    seconds: int = 0, minutes: int = 0, hours: int = 0, job_id: str = None
):
    """装饰器：注册为间隔定时任务

    Args:
        seconds: 间隔秒数
        minutes: 间隔分钟数
        hours: 间隔小时数
        job_id: 任务ID，默认使用函数名

    Usage:
        @interval_job(minutes=5, job_id="cleanup")
        async def my_task():
            print("Running task...")
    """

    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        # 注册任务
        scheduler_manager.add_interval_job(
            func=wrapper,
            seconds=seconds,
            minutes=minutes,
            hours=hours,
            id=job_id or func.__name__,
        )

        return wrapper

    return decorator


# 装饰器：定时任务（Cron 表达式触发）
def cron_job(cron_expression: str, job_id: str = None):
    """装饰器：注册为 Cron 定时任务

    Args:
        cron_expression: Cron 表达式，例如: "*/5 * * * *" (每5分钟)
        job_id: 任务ID，默认使用函数名

    Usage:
        @cron_job("*/5 * * * *", job_id="cleanup")
        async def my_task():
            print("Running task...")
    """

    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        # 注册任务
        scheduler_manager.add_cron_job(
            func=wrapper, cron_expression=cron_expression, id=job_id or func.__name__
        )

        return wrapper

    return decorator
