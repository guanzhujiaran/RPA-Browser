"""后台任务注册和配置"""

from loguru import logger
from app.config import settings
from app.scheduler_manager import scheduler_manager_ist
from app.services.RPA_browser.background_tasks import BackgroundTasks
from app.services.RPA_browser.session.launch_queue import get_launch_queue


def register_background_tasks():
    """注册所有后台任务"""
    # 统一清理任务 - 每 browser_session_cleanup_interval 秒执行一次（默认 60s）
    # 整合了所有清理逻辑：闲置三级软着陆（降级/挂起/关闭）、过期清理
    scheduler_manager_ist.add_interval_job(
        func=BackgroundTasks.cleanup_all_sessions,
        seconds=settings.browser_session_cleanup_interval,
        id="cleanup_all_sessions",
        name="会话清理任务",
        misfire_grace_time=None,  # 错过执行时间不立即执行,等待下一次
    )

    # 观看者回收任务 - 每 browser_webrtc_viewer_reap_interval 秒执行一次（默认 20s）
    # 回收心跳超时的观看者连接（前端关标签页 / 断网时不会主动发关闭请求）
    # 见 docs/rpa-多观看者并发直播计划书.md §4.3
    scheduler_manager_ist.add_interval_job(
        func=BackgroundTasks.reap_idle_viewers,
        seconds=settings.browser_webrtc_viewer_reap_interval,
        id="reap_idle_viewers",
        name="观看者回收任务",
        misfire_grace_time=None,
    )

    logger.info("✅ All background tasks registered")
    logger.info("📋 Registered tasks:")
    for job in scheduler_manager_ist.get_jobs():
        logger.info(f"  - {job.name} (ID: {job.id})")


async def start_background_tasks():
    """启动所有后台任务"""
    logger.info("🚀 Starting background tasks...")

    # 启动浏览器启动队列的兜底轮询（内存释放后自动放行排队请求）
    get_launch_queue().start()

    # 注册所有后台任务
    register_background_tasks()

    # 按库重建工作流定时任务（调度外壳：启用 + cron + 已指定浏览器）
    from app.services.execution.workflow_scheduler import register_all_workflow_jobs

    await register_all_workflow_jobs()

    # 启动调度器
    scheduler_manager_ist.start()

    logger.info("✅ Background tasks started successfully")


async def stop_background_tasks():
    """停止所有后台任务"""
    logger.info("🛑 Stopping background tasks...")

    # 停止浏览器启动队列轮询
    await get_launch_queue().stop()

    # 关闭调度器
    scheduler_manager_ist.shutdown(wait=True)

    logger.info("✅ Background tasks stopped successfully")
