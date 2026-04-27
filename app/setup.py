"""后台任务注册和配置"""

from loguru import logger
from app.scheduler_manager import scheduler_manager
from app.services.RPA_browser.background_tasks import BackgroundTasks


def register_background_tasks():
    """注册所有后台任务"""
    # 统一清理任务 - 每 5 分钟执行一次
    # 整合了所有清理逻辑：心跳检查、直播流超时、闲置清理、过期清理
    scheduler_manager.add_interval_job(
        func=BackgroundTasks.cleanup_all_sessions,
        minutes=5,
        id="cleanup_all_sessions",
        name="会话清理任务",
        misfire_grace_time=None,  # 错过执行时间不立即执行,等待下一次
    )

    logger.info("✅ All background tasks registered")
    logger.info("📋 Registered tasks:")
    for job in scheduler_manager.get_jobs():
        logger.info(f"  - {job.name} (ID: {job.id})")


async def start_background_tasks():
    """启动所有后台任务"""
    logger.info("🚀 Starting background tasks...")

    # 注册所有后台任务
    register_background_tasks()

    # 启动调度器
    scheduler_manager.start()

    logger.info("✅ Background tasks started successfully")


async def stop_background_tasks():
    """停止所有后台任务"""
    logger.info("🛑 Stopping background tasks...")

    # 关闭调度器
    scheduler_manager.shutdown(wait=True)

    logger.info("✅ Background tasks stopped successfully")
