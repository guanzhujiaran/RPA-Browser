import asyncio
import uuid
import sys
from app.models.RPA_browser.browser_info_model import UserBrowserInfoCreateParams
from app.models.RPA_browser.browser_session_model import SessionCreateParams, BrowserSessionRemoveParams
from app.services.RPA_browser.browser_session_pool.playwright_pool import get_default_session_pool
from app.services.RPA_browser.browser_db_service import BrowserDBService
from app.utils.depends.session_manager import DatabaseSessionManager

# Windows平台需要设置事件循环策略
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def test_plugined_browser():
    """
    测试如何使用PlaywrightSessionPool获取包含插件的浏览器会话
    """
    # 生成一个唯一的browser_token
    browser_token = uuid.uuid4()
    print(f"Browser Token: {browser_token}")

    # 创建浏览器指纹信息
    async with DatabaseSessionManager.async_session() as session:
        fingerprint_params = UserBrowserInfoCreateParams(
            browser_token=browser_token,
            fingerprint_int=12345  # 示例指纹值
        )
        created_fingerprint = await BrowserDBService.create_fingerprint(
            params=fingerprint_params,
            session=session
        )
        browser_id = created_fingerprint.id
        print(f"Created browser fingerprint with ID: {browser_id}")

    # 获取默认的会话池
    session_pool = get_default_session_pool()

    # 创建会话参数
    session_params = SessionCreateParams(
        browser_token=browser_token,
        browser_id=browser_id,
        headless=True  # 在测试环境中通常使用无头模式
    )

    # 获取包含插件的浏览器会话
    plugined_session = await session_pool.get_session(session_params)
    print(f"Got session for browser_id: {browser_id}")

    try:
        # 获取当前页面
        page = await plugined_session.get_current_page()
        print("Got current page")

        # 使用页面执行一些操作（例如访问网页）
        response = await page.goto("https://httpbin.org/get")
        print(f"Visited page: {response.url}")

        # 获取页面标题
        title = await page.title()
        print(f"Page title: {title}")

        # 执行其他操作...
        print("Performing test operations...")

    except Exception as e:
        print(f"Error during browser operations: {e}")
    finally:
        # 释放会话资源
        print("Closing session...")
        release_params = BrowserSessionRemoveParams(
            browser_token=browser_token,
            browser_id=browser_id,
            force_close=True
        )
        result = await session_pool.release_session(release_params)
        print(f"Session closed: {result.is_closed}, Feedback: {result.feedback}")

if __name__ == "__main__":
    asyncio.run(test_plugined_browser())