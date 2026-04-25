"""后台任务服务 - 处理浏览器操作的异步后台任务"""
import asyncio
from typing import Dict, Any
from loguru import logger
from app.services.RPA_browser.live_service import LiveService


class BackgroundTaskService:
    """后台任务服务"""

    # 存储正在运行的任务
    running_tasks: Dict[str, asyncio.Task] = {}

    @staticmethod
    def _get_task_key(mid: int, browser_id: int, operation_type: str) -> str:
        """获取任务键"""
        return f"{mid}_{browser_id}_{operation_type}"
    
    @staticmethod
    async def navigate_to_url_background(mid: int, browser_id: int, url: str) -> Dict[str, Any]:
        """后台导航到指定URL"""
        task_key = BackgroundTaskService._get_task_key(mid, browser_id, "navigate")
        
        # 检查是否已有相同任务在运行
        if task_key in BackgroundTaskService.running_tasks:
            existing_task = BackgroundTaskService.running_tasks[task_key]
            if not existing_task.done():
                logger.info(f"导航任务 {task_key} 已在运行中，跳过重复执行")
                return {"message": "导航任务已在处理中", "status": "processing"}
            else:
                # 清理已完成的任务
                del BackgroundTaskService.running_tasks[task_key]
        
        try:
            logger.info(f"开始后台导航任务: {task_key}, URL: {url}")
            
            # 获取浏览器会话
            plugined_session = await LiveService.get_plugined_session(mid, browser_id, headless=False)
            page = await plugined_session.get_current_page()
            
            if url:
                await page.goto(url)
                result = {"message": f"已导航到: {url}", "status": "completed"}
                logger.info(f"后台导航任务完成: {task_key}")
            else:
                result = {"error": "URL 不能为空", "status": "failed"}
                logger.error(f"后台导航任务失败: {task_key} - URL为空")
            
            return result
            
        except Exception as e:
            logger.error(f"后台导航任务异常: {task_key} - {e}")
            return {"error": str(e), "status": "failed"}
        finally:
            # 清理任务
            if task_key in BackgroundTaskService.running_tasks:
                del BackgroundTaskService.running_tasks[task_key]
    
    @staticmethod
    async def evaluate_javascript_background(mid: int, browser_id: int, code: str) -> Dict[str, Any]:
        """后台执行JavaScript代码"""
        task_key = BackgroundTaskService._get_task_key(mid, browser_id, "evaluate")
        
        # 检查是否已有相同任务在运行
        if task_key in BackgroundTaskService.running_tasks:
            existing_task = BackgroundTaskService.running_tasks[task_key]
            if not existing_task.done():
                logger.info(f"JavaScript执行任务 {task_key} 已在运行中，跳过重复执行")
                return {"message": "JavaScript执行任务已在处理中", "status": "processing"}
            else:
                # 清理已完成的任务
                del BackgroundTaskService.running_tasks[task_key]
        
        try:
            logger.info(f"开始后台JavaScript执行任务: {task_key}")
            
            # 获取浏览器会话
            plugined_session = await LiveService.get_plugined_session(mid, browser_id, headless=False)
            page = await plugined_session.get_current_page()
            
            if code:
                # 检查是否为异步函数
                if code.strip().startswith("(async"):
                    exec_result = await page.evaluate_handle(code)
                    if hasattr(exec_result, "__await__"):
                        result_value = await exec_result
                    else:
                        result_value = exec_result
                else:
                    result_value = await page.evaluate(code)
                
                result = {"result": result_value, "status": "completed"}
                logger.info(f"后台JavaScript执行任务完成: {task_key}")
            else:
                result = {"error": "代码不能为空", "status": "failed"}
                logger.error(f"后台JavaScript执行任务失败: {task_key} - 代码为空")
            
            return result
            
        except Exception as e:
            logger.error(f"后台JavaScript执行任务异常: {task_key} - {e}")
            return {"error": str(e), "status": "failed"}
        finally:
            # 清理任务
            if task_key in BackgroundTaskService.running_tasks:
                del BackgroundTaskService.running_tasks[task_key]
    
    @staticmethod
    async def click_background(mid: int, browser_id: int, click_params: Dict[str, Any]) -> Dict[str, Any]:
        """后台执行点击操作"""
        task_key = BackgroundTaskService._get_task_key(mid, browser_id, "click")
        
        # 检查是否已有相同任务在运行
        if task_key in BackgroundTaskService.running_tasks:
            existing_task = BackgroundTaskService.running_tasks[task_key]
            if not existing_task.done():
                logger.info(f"点击任务 {task_key} 已在运行中，跳过重复执行")
                return {"message": "点击任务已在处理中", "status": "processing"}
            else:
                # 清理已完成的任务
                del BackgroundTaskService.running_tasks[task_key]
        
        try:
            logger.info(f"开始后台点击任务: {task_key}, params: {click_params}")
            
            # 获取浏览器会话
            plugined_session = await LiveService.get_plugined_session(mid, browser_id, headless=False)
            page = await plugined_session.get_current_page()
            
            # 获取页面视口大小
            viewport = page.viewport_size
            if not viewport:
                # 如果没有设置视口，获取页面尺寸
                viewport = await page.evaluate(
                    """
                () => ({
                    width: window.innerWidth || document.documentElement.clientWidth,
                    height: window.innerHeight || document.documentElement.clientHeight
                })
                """
                )

            # 计算绝对坐标
            abs_x = int(click_params["x"] * viewport["width"])
            abs_y = int(click_params["y"] * viewport["height"])

            # 执行点击操作
            if click_params.get("double", False):
                await page.dblclick(x=abs_x, y=abs_y, button=click_params.get("button", "left"))
            else:
                await page.click(x=abs_x, y=abs_y, button=click_params.get("button", "left"))

            # 等待指定时间
            wait_after = click_params.get("wait_after", 0)
            if wait_after > 0:
                await asyncio.sleep(wait_after / 1000)

            result = {
                "message": f"点击操作执行成功", 
                "status": "completed",
                "coordinates": {
                    "relative": {"x": click_params["x"], "y": click_params["y"]},
                    "absolute": {"x": abs_x, "y": abs_y},
                    "viewport": viewport
                }
            }
            logger.info(f"后台点击任务完成: {task_key}")
            
            return result
            
        except Exception as e:
            logger.error(f"后台点击任务异常: {task_key} - {e}")
            return {"error": str(e), "status": "failed"}
        finally:
            # 清理任务
            if task_key in BackgroundTaskService.running_tasks:
                del BackgroundTaskService.running_tasks[task_key]
    
    @staticmethod
    async def execute_browser_command_background(mid: int, browser_id: int, command: Dict[str, Any]) -> Dict[str, Any]:
        """后台执行浏览器命令"""
        task_key = BackgroundTaskService._get_task_key(mid, browser_id, "command")
        
        # 检查是否已有相同任务在运行
        if task_key in BackgroundTaskService.running_tasks:
            existing_task = BackgroundTaskService.running_tasks[task_key]
            if not existing_task.done():
                logger.info(f"浏览器命令任务 {task_key} 已在运行中，跳过重复执行")
                return {"message": "浏览器命令任务已在处理中", "status": "processing"}
            else:
                # 清理已完成的任务
                del BackgroundTaskService.running_tasks[task_key]
        
        try:
            logger.info(f"开始后台浏览器命令任务: {task_key}, command: {command}")
            
            # 使用现有的服务执行命令
            result = await LiveService.execute_browser_command(mid, browser_id, command)
            
            logger.info(f"后台浏览器命令任务完成: {task_key}")
            return {"result": result, "status": "completed"}
            
        except Exception as e:
            logger.error(f"后台浏览器命令任务异常: {task_key} - {e}")
            return {"error": str(e), "status": "failed"}
        finally:
            # 清理任务
            if task_key in BackgroundTaskService.running_tasks:
                del BackgroundTaskService.running_tasks[task_key]


class BackgroundTasks:
    """后台任务管理类 - 用于调度器的定时任务"""

    @staticmethod
    async def cleanup_expired_resources():
        """清理过期资源 - 每5分钟执行一次"""
        try:
            logger.info("🧹 Starting cleanup_expired_resources task")
            await LiveService.cleanup_expired_sessions()
            await LiveService._cleanup_idle_browsers()
            logger.info("✅ cleanup_expired_resources task completed")
        except Exception as e:
            logger.error(f"❌ cleanup_expired_resources task failed: {e}")

    @staticmethod
    async def check_heartbeat_timeouts():
        """检查心跳超时 - 每5分钟执行一次"""
        try:
            logger.info("💓 Starting check_heartbeat_timeouts task")
            # 检查会话心跳超时
            await LiveService._check_heartbeat_timeouts()
            # 检查直播流超时
            await LiveService._check_live_stream_timeouts()
            logger.info("✅ check_heartbeat_timeouts task completed")
        except Exception as e:
            logger.error(f"❌ check_heartbeat_timeouts task failed: {e}")
