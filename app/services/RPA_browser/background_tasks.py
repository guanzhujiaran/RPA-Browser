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
        
    
