import asyncio
import time
import uuid
import logging

from patchright.async_api import Page

from app.services.RPA_browser.base.base_engines import BaseUndetectedPlaywright

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

lock = asyncio.Lock()
start_ts = int(time.time())

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
        'canvas': 'https://www.browserscan.net/zh/canvas',
        'bot_scan': 'https://www.browserscan.net/zh',
        'web_gpu': 'https://www.browserscan.net/zh/webgpu',
        'fingerprint_playground': 'https://demo.fingerprint.com/playground'  # 暂时注释掉有问题的网站
    }

    def __init__(self, pg: Page, browser_token):
        self.pg = pg
        self.browser_token = browser_token
        self.test_lock = asyncio.Lock()

    async def run_test(self):
        async def test_site(test_name, site):
            async with self.test_lock:
                try:
                    logger.info(f'{test_name}: {site}开始测试')
                    await self.pg.goto(site)
                    await asyncio.sleep(5)
                    # 更具体的选择器，避免匹配多个元素
                    agree_btn_selector = 'button.fc-primary-button[aria-label="同意"]'
                    if await self.pg.locator(agree_btn_selector).first.is_visible():
                        await self.pg.locator(agree_btn_selector).first.click()
                    logger.info(f'{test_name}: {site}开始截图')
                    await self.pg.screenshot(
                        path=f'./{test_name}/_{self.browser_token}_{start_ts}.png'.replace('-', '_'),
                        full_page=True
                    )
                    logger.info(f'{test_name}: {site}测试完成')
                except Exception as e:
                    logger.error(f'{test_name}: {site}测试失败: {str(e)}')
                    # 即使某个测试失败，也不影响其他测试的执行

        tasks = []
        for k, v in self.test_sites_dict.items():
            task = test_site(k, v)
            tasks.append(task)
        
        # 并发执行所有测试任务
        await asyncio.gather(*tasks, return_exceptions=True)


async def op_browser(browser_token: uuid.UUID | str):
    async with BaseUndetectedPlaywright(
            browser_token=browser_token,
            headless=True,
    ).launch_browser_span() as browser:
        page = await browser.new_page()
        bot_scan = BotScan(page, browser_token)
        await bot_scan.run_test()
        await browser.close()


async def main(browser_token: uuid.UUID | str):
    await op_browser(browser_token)


async def _test_main():
    for i in browser_token_list:
        await main(i)
    # await asyncio.gather(*[main(i) for i in browser_token_list])


if __name__ == '__main__':
    asyncio.run(_test_main())
