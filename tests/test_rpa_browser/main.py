import asyncio
import os
import random
import time
import uuid

from playwright.async_api import Page
from loguru import logger

from app.models.RPA_browser.browser_info_model import (
    UserBrowserInfoListParams,
    UserBrowserInfoCreateParams,
)
from app.models.RPA_browser.browser_session_model import SessionCreateParams
from app.services.RPA_browser.browser_db_service import BrowserDBService
from app.services.RPA_browser.browser_session_pool.playwright_pool import (
    get_default_session_pool,
)
from app.utils.depends.session_manager import DatabaseSessionManager

start_ts = int(time.time())
cur_dir = os.path.dirname(os.path.abspath(__file__))
browser_token_list = [
    "0fc69198bccb4b00928b9372c99190b7",
    "1fcf72cd31c54add833ad8488473ef8d",
    "2f888ba1798c49b4b1198afb3de27f82",
    "375b3861209747e98911e914eca835cd",
    "480fd0d41c9d445e84ef0798ddd775f7",
    "66e3d1f440324a47834c6877ec9f41a2",
    "8ef36564b5294a0382e881d31fa01fc9",
    "bc80bea0794645c3ac89e4cb122784db",
    "eab6a1a6fe704658ac6bc932ba73301c",
    "ec798127c07549969e0d163eb335026f",
]


class BotScan:
    test_sites_dict = {
        "canvas": "https://www.browserscan.net/zh/canvas",
        "bot_scan": "https://www.browserscan.net/zh",
        "web_gpu": "https://www.browserscan.net/zh/webgpu",
        "fingerprint_playground": "https://demo.fingerprint.com/playground",  # 暂时注释掉有问题的网站
        "rebrowser_bot_detector": "https://bot-detector.rebrowser.net",
    }

    def __init__(self, pg: Page, browser_token: uuid.UUID):
        self.pg = pg
        self.browser_token = browser_token

    async def run_test(self):
        async def test_site(test_name, site):
            try:
                logger.info(f"{test_name}: {site}开始测试")
                await self.pg.goto(site)
                await asyncio.sleep(5)
                # 更具体的选择器，避免匹配多个元素
                agree_btn_selector = 'button.fc-primary-button[aria-label="同意"]'
                if await self.pg.locator(agree_btn_selector).first.is_visible():
                    await self.pg.locator(agree_btn_selector).first.click()
                logger.info(f"{test_name}: {site}开始截图")
                _ = await self.pg.screenshot(
                    path=os.path.join(
                        cur_dir,
                        f"./{test_name}/_{self.browser_token}_{start_ts}.png".replace(
                            "-", "_"
                        ),
                    ),
                    full_page=True,
                )
                logger.info(f"{test_name}: {site}测试完成")
            except Exception as e:
                logger.error(f"{test_name}: {site}测试失败: {str(e)}")
                # 即使某个测试失败，也不影响其他测试的执行

        tasks = []
        for k, v in self.test_sites_dict.items():
            task = test_site(k, v)
            tasks.append(task)

        # 并发执行所有测试任务
        await asyncio.gather(*tasks, return_exceptions=True)


async def op_browser(browser_token: uuid.UUID):
    # 使用托管会话来确保正确的事务管理
    print(f"执行测试：{browser_token}")
    async with DatabaseSessionManager.async_session() as session:
        browser_list = await BrowserDBService.list_fingerprint(
            UserBrowserInfoListParams(browser_token=browser_token, page=1, per_page=10),
            session,
        )

        if browser_list.items:
            browser_id = browser_list.items[0].id
        else:
            created_fingerprint = await BrowserDBService.create_fingerprint(
                UserBrowserInfoCreateParams(
                    browser_token=browser_token, fingerprint_int=random.randint(0, 9999)
                ),
                session,
            )
            browser_id = created_fingerprint.id

    # 获取会话池
    session_pool = get_default_session_pool()

    # 创建会话参数
    session_params = SessionCreateParams(
        browser_token=browser_token, browser_id=browser_id, headless=True
    )
    print(f'session_params: {session_params}')
    # 获取包含插件的会话
    plugined_session = await session_pool.get_session(session_params)
    print(f'plugined_session: {plugined_session}')
    page = await plugined_session.get_current_page()
    bot_scan = BotScan(page, browser_token)
    await bot_scan.run_test()
    await plugined_session.close()


async def main(browser_token: uuid.UUID):
    await op_browser(browser_token)


async def _test_main():
    await asyncio.gather(*[main(i) for i in browser_token_list])


if __name__ == "__main__":
    asyncio.run(_test_main())
