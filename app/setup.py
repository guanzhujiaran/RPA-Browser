"""后台任务注册和配置"""

import asyncio

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

    # 僵尸运行收口 - 每 workflow_stale_run_reap_interval 秒执行一次（默认 300s）
    # 清理「进程重启 / 执行会话丢失 / 协程被取消」后仍停留在 RUNNING 的运行记录，
    # 否则同浏览器并发防护会把已死运行当作执行中，永久拦截该浏览器的新运行。
    from app.services.execution.workflow_runner import reap_zombie_runs

    scheduler_manager_ist.add_interval_job(
        func=reap_zombie_runs,
        seconds=settings.workflow_stale_run_reap_interval,
        id="workflow_reap_zombie_runs",
        name="僵尸运行收口",
        misfire_grace_time=None,
        max_instances=1,
        coalesce=True,
    )

    # 停止宽限期强制终止 - 每 workflow_stop_scan_interval 秒执行一次（默认 10s）
    # 两阶段停止的第二阶段：请求停止后若超宽限期仍未收口，强制 cancel 运行 task
    # （等价 SIGKILL），避免运行记录长期停留在 RUNNING 拦截同浏览器的新运行。
    from app.services.execution.workflow_runner import enforce_stop_grace

    scheduler_manager_ist.add_interval_job(
        func=enforce_stop_grace,
        seconds=settings.workflow_stop_scan_interval,
        id="workflow_enforce_stop_grace",
        name="停止宽限期强制终止",
        misfire_grace_time=None,
        max_instances=1,
        coalesce=True,
    )

    # 操作日志过期清理 - 每 action_log_cleanup_interval 秒执行一次（默认 24h）
    # 按 started_at 删除超过 settings.action_log_default_retention_days（默认 7 天）
    # 的步骤级操作日志，防止日志表无限增长占满硬盘；retention=0 时任务空转。
    from app.services.execution.crud_service import action_log_crud_svr

    scheduler_manager_ist.add_interval_job(
        func=action_log_crud_svr.cleanup_expired,
        seconds=settings.action_log_cleanup_interval,
        id="action_log_cleanup",
        name="操作日志过期清理",
        misfire_grace_time=None,
        max_instances=1,
        coalesce=True,
    )

    # Fork 上游自动同步 - 每 10 分钟执行一次（默认）
    # 扫描开启了 auto_sync_upstream 开关且上游有新版本的 Fork 副本，逐个同步并通知；
    # 本地修改过的副本跳过并通知，绝不自动覆盖（docs/fork-上游同步计划书.md §4.4）
    from app.services.execution.upstream_sync_service import ForkAutoSyncService

    scheduler_manager_ist.add_interval_job(
        func=ForkAutoSyncService.scan_and_sync_all,
        seconds=settings.workflow_upstream_sync_interval,
        id="fork_upstream_auto_sync",
        name="Fork 上游自动同步",
        misfire_grace_time=None,
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

    # 注册运行状态事件订阅者（docs/工作流运行状态机计划书.md §7）：
    # 必须在启动恢复（下面对 RUNNING 记录的收口）之前注册，否则启动期收口产生的事件
    # 没人消费，工作流卡片的 last_run_status 会滞后。
    from app.services.execution.run_event_handlers import (
        register_default_run_event_handlers,
    )

    register_default_run_event_handlers()

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

    # 关闭调度器（不再产生新的定时运行）
    scheduler_manager_ist.shutdown(wait=True)

    # 优雅停机清扫：取消仍在执行的工作流运行 task，使其走 run_workflow 的
    # except BaseException 收口路径（落 CANCELLED/INTERRUPTED），避免下次启动
    # 靠启动恢复兜底（kill -9 等非优雅关停仍由启动恢复负责）。
    await _cancel_running_workflow_tasks()

    logger.info("✅ Background tasks stopped successfully")


async def _cancel_running_workflow_tasks() -> None:
    """取消本实例所有仍在执行的工作流运行 task，并限时等待其收口"""
    try:
        from app.services.execution.run_tasks import workflow_run_tasks

        run_ids = workflow_run_tasks.live_run_ids()
        if not run_ids:
            return
        tasks = [t for t in (workflow_run_tasks.get(rid) for rid in run_ids) if t]
        for rid in run_ids:
            workflow_run_tasks.cancel(rid)
        if tasks:
            await asyncio.wait(tasks, timeout=10)
        logger.info(f"已收口 {len(run_ids)} 个在跑工作流运行 task")
    except Exception as exc:  # noqa: BLE001 - 停机清扫失败不阻塞关停
        logger.warning(f"停机清扫在跑工作流运行 task 失败: {exc}")
