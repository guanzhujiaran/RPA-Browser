import time
import uuid
import json
import asyncio
from typing import Optional
from dataclasses import dataclass
from typing import Dict

from app.models.RPA_browser.browser_info_model import UserBrowserInfoReadParams
from app.utils.depends.session_manager import DatabaseSessionManager
from app.services.RPA_browser.browser_db_service import BrowserDBService
from app.services.RPA_browser.browser_session_pool.playwright_pool import get_default_session_pool

# TODO 把这个地方的代码全部和plugined_session整合在一起

@dataclass
class LiveSessionEntry:
    browser_token: str
    headless: bool
    browser_id: str = None  # 添加browser_id字段
    page: Optional[object] = None
    ts: int = 0


class LiveService:
    # 维护 live 会话状态
    live_sessions: Dict[str, LiveSessionEntry] = {}

    @staticmethod
    async def validate_browser_token(browser_token: uuid.UUID) -> bool:
        async with DatabaseSessionManager.async_session() as session:
            record = await BrowserDBService.read_fingerprint(
                params=UserBrowserInfoReadParams(browser_token=browser_token),
                session=session
            )
            return record is not None

    @staticmethod
    async def create_live_session(browser_token: uuid.UUID, browser_id: str = None, headless: bool = True) -> str:
        """
        创建直播会话
        
        Args:
            browser_token: 浏览器令牌
            browser_id: 浏览器实例ID
            headless: 是否无头模式
            
        Returns:
            str: 会话ID
        """
        # 生成会话ID
        live_id = str(uuid.uuid4())
        
        # 如果没有提供browser_id，则生成一个
        if not browser_id:
            browser_id = f"live_{int(time.time())}_{live_id[:8]}"
            
        # 创建会话条目
        entry = LiveSessionEntry(
            browser_token=str(browser_token),
            headless=headless,
            browser_id=browser_id,  # 保存browser_id
            ts=int(time.time())
        )
        
        # 存储会话
        LiveService.live_sessions[live_id] = entry
        
        return live_id

    @staticmethod
    def get_live_entry(live_id: str) -> Optional[LiveSessionEntry]:
        # 使用 browser_token 作为键来获取会话
        return LiveService.live_sessions.get(live_id)

    @staticmethod
    async def get_page_for_entry(entry: LiveSessionEntry):
        # 复用/创建并缓存页面对象
        try:
            page = entry.page
            if page is not None:
                try:
                    # 简单探测页面可用性
                    _ = getattr(page, 'is_closed', None)
                    if callable(_):
                        if not page.is_closed():
                            return page
                except Exception:
                    pass
        except Exception:
            pass
        headless = entry.headless
        browser_token = uuid.UUID(entry.browser_token)
        pool = get_default_session_pool()
        # 使用存储的browser_id
        page = await pool.get_page(browser_token, browser_id=entry.browser_id, headless=headless)
        entry.page = page
        return page

    @staticmethod
    async def generate_video_stream(entry):
        """生成视频流数据"""
        page = await LiveService.get_page_for_entry(entry)
        
        async def frame_generator():
            try:
                while True:
                    img_bytes = await page.screenshot(full_page=False, type='jpeg', quality=60)
                    yield b"--frame\r\n" \
                        + b"Content-Type: image/jpeg\r\n" \
                        + f"Content-Length: {len(img_bytes)}\r\n\r\n".encode('ascii') \
                        + img_bytes + b"\r\n"
                    await asyncio.sleep(0.2)
            except Exception:
                pass

        return frame_generator

    @staticmethod
    async def handle_websocket_message(websocket, page, message: str):
        """处理WebSocket消息"""
        try:
            data = json.loads(message)
        except Exception:
            await websocket.send_text(json.dumps({'type': 'error', 'payload': 'invalid json'}))
            return

        msg_type = data.get('type')
        if msg_type == 'eval':
            code = data.get('code', '')
            try:
                # 如果代码中包含 await page，则使用 page.evaluate 并支持 Playwright API
                if code.strip().startswith('await page'):
                    # 执行 Playwright 命令
                    # 创建一个局部变量上下文，将 page 对象注入其中
                    context = {'page': page}
                    exec_result = eval(code, context)
                    if hasattr(exec_result, '__await__'):
                        result = await exec_result
                    else:
                        result = exec_result
                else:
                    # 传统的 evaluate 执行方式
                    result = await page.evaluate(code)

                # 结果尽量可序列化
                await websocket.send_text(json.dumps({
                    'type': 'eval_result',
                    'payload': result if isinstance(result, (str, int, float, bool, type(None))) else str(result)
                }))
            except Exception as e:
                await websocket.send_text(json.dumps({'type': 'error', 'payload': str(e)}))
        elif msg_type == 'navigate':
            url = data.get('url', '')
            if url:
                try:
                    await page.goto(url)
                    await websocket.send_text(json.dumps({'type': 'info', 'payload': f'已导航到: {url}'}))
                except Exception as e:
                    await websocket.send_text(json.dumps({'type': 'error', 'payload': str(e)}))
            else:
                await websocket.send_text(json.dumps({'type': 'error', 'payload': 'URL 不能为空'}))
        else:
            await websocket.send_text(json.dumps({'type': 'error', 'payload': 'unknown message type'}))

    @staticmethod
    async def stop_live_session(browser_token: uuid.UUID) -> bool:
        """
        停止直播会话
        
        Args:
            browser_token: 浏览器令牌
            
        Returns:
            bool: 是否成功停止
        """
        pool = get_default_session_pool()
        try:
            await pool.release_all_session(browser_token)
            return True
        except Exception:
            return False