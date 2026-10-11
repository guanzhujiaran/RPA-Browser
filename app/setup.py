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

    # 工作流计费心跳 - 每 workflow_billing_heartbeat_interval 秒执行一次（默认 300s）
    # 对运行中的定时任务按差额预扣；余额耗尽熔断；会话丢失兜底结算
    # 见 docs/浏览器使用时长与会员权益计划书.md §8
    from app.services.membership.membership_service import MembershipService

    scheduler_manager_ist.add_interval_job(
        func=MembershipService.run_billing_heartbeat,
        seconds=settings.workflow_billing_heartbeat_interval,
        id="workflow_billing_heartbeat",
        name="工作流计费心跳",
        misfire_grace_time=None,
        # 单进程内严格串行，避免上一轮未跑完就叠跑下一轮。
        # 跨进程（多实例）由 MembershipService 的「差额式幂等预扣」兜底：
        # 每个 run 只扣到当前墙钟分钟的目标额度，重复执行不会重复扣费。
        max_instances=1,
        coalesce=True,
    )

    # 赠送余额月初清零 - 每月 1 号 00:05（错开整点，避开月末计费心跳高峰）
    # 把上月所有账户「赠送桶」（签到/活动）余额清零，付费桶（时长卡/购买）保留；
    # 批次表 period 唯一保证幂等
    if settings.duration_monthly_reset_enabled:
        scheduler_manager_ist.add_cron_job(
            func=MembershipService.run_monthly_reset,
            cron_expression="5 0 1 * *",
            id="duration_monthly_reset",
            name="剩余时长月初清零",
            replace_existing=True,
            misfire_grace_time=3600,  # 停机错过后 1 小时内仍补执行
            max_instances=1,
            coalesce=True,
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

    # 崩溃恢复：先把上次进程残留的 RUNNING 运行记录收口为 INTERRUPTED，
    # 否则同浏览器并发防护（browser_id + RUNNING）会把已死运行误判为执行中，
    # 导致该浏览器的工作流永远无法再启动。必须在重建定时任务之前完成。
    from app.services.execution.crud_service import workflow_run_crud_svr

    try:
        stale_runs = await workflow_run_crud_svr.interrupt_stale_running()
        if stale_runs:
            logger.warning(
                f"⚠️ 检测到 {len(stale_runs)} 条因进程重启中断的工作流运行，"
                f"已收口为 INTERRUPTED: {[r.run_id for r in stale_runs][:20]}"
            )
        else:
            logger.info("✅ 无残留 RUNNING 工作流运行记录")
    except Exception as exc:  # noqa: BLE001 - 恢复失败不阻塞服务启动
        logger.exception(f"收口残留 RUNNING 运行记录失败（不阻塞启动）: {exc}")

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
