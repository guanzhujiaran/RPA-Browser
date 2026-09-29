"""后台任务服务 - 处理浏览器操作的异步后台任务"""

from app.services.RPA_browser.session.live_service import live_service
from loguru import logger
from app.config import settings, ConfigRunningModeEnum


class BackgroundTasks:
    """后台任务管理类 - 用于调度器的定时任务"""

    @staticmethod
    async def cleanup_all_sessions():
        """
        统一清理任务 - 每5分钟执行一次
        
        整合所有清理逻辑到一个任务中，包括：
        1. 会话状态检查（含闲置三级软着陆与自动清理）

        注意：
        - 状态机 _check_session_cleanup() 已经处理了：
          * 过期会话清理 (expires_at)
          * 闲置三级软着陆：降级（降质降帧）→ 挂起（关流保实例）→ 关闭（含宽限期）
          * 自动化任务占用 (pin) 期间跳过一切降级/关闭
        
        优势：
        - 减少任务数量，降低调度复杂度
        - 避免重复遍历会话列表
        - 统一的日志输出
        - 更容易监控和维护
        """
        logger.info("🧹 开始执行会话清理任务")
        if settings.RUNNING_MODE == ConfigRunningModeEnum.PROD:
            # 1. 会话状态检查（包含状态机评估和自动清理）
            await live_service._check_session_cleanup()
            
            logger.info("✅ 会话清理任务完成")
        else:
            logger.info("开发者模式下不清理浏览器会话")

    @staticmethod
    async def reap_idle_viewers():
        """回收心跳超时的观看者 —— 多观看者并发直播

        前端直接关标签页 / 断网时不会发关闭请求，只能靠心跳超时回收，
        否则会一直占着一份 H264 编码资源。

        间隔由 `browser_webrtc_viewer_reap_interval` 控制（默认 20s），
        回收阈值为 `browser_webrtc_viewer_idle_timeout`（默认 60s）。
        详见 docs/rpa-多观看者并发直播计划书.md §4.3。

        ⚠️ 与「会话清理」不同，本任务**不按运行模式跳过**：
        观看者数是对外可见的展示数据（「N 人在观看」），一旦有僵尸观看者残留，
        开发者模式下永远不会回收，人数会随每次重连 / 刷新一路涨上去，
        表现为「一个人的重连被算成多人在看」。回收只针对
        `pc.connectionState != connected` 且超时的观看者，不影响正常观看。
        """
        timeout = settings.browser_webrtc_viewer_idle_timeout
        reaped = 0
        for session_key, entry in list(live_service._browser_sessions.items()):
            manager = getattr(entry.browser_session, "webrtc_manager", None)
            if manager is None or manager.viewer_count == 0:
                continue
            try:
                reaped_in_session = await manager.reap_idle_viewers(timeout)
                reaped += reaped_in_session
                if reaped_in_session:
                    # 被回收的观看者必须从 SSE 订阅者的人数 / 列表里移除：
                    # 心跳下线后没有其他周期性触发点（见计划书 §10.7）
                    live_service.notify_session_status(entry.mid, entry.browser_id)
            except Exception as e:  # noqa: BLE001 - 单个会话失败不应中断整轮扫描
                logger.error(f"回收观看者失败: {session_key}, error: {e}")

        if reaped:
            logger.info(f"🧹 观看者回收完成：本次回收 {reaped} 个观看者连接")
        
    
