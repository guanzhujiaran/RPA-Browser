"""后台任务注册和配置"""
from loguru import logger
from app.scheduler_manager import scheduler_manager
from app.services.RPA_browser.background_tasks import BackgroundTasks


def register_background_tasks():
    """注册所有后台任务"""

    # 1. 资源清理任务 - 每 5 分钟执行一次
    scheduler_manager.add_interval_job(
        func=BackgroundTasks.cleanup_expired_resources,
        minutes=5,
        id="cleanup_resources",
        name="清理过期资源",
        misfire_grace_time=None,  # 错过执行时间不立即执行,等待下一次
    )

    # 2. 心跳检查任务 - 每 5 分钟执行一次
    scheduler_manager.add_interval_job(
        func=BackgroundTasks.check_heartbeat_timeouts,
        minutes=5,
        id="check_heartbeat",
        name="检查心跳超时",
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
