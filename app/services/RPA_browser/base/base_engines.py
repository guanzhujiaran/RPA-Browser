import os
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

from playwright.async_api import BrowserContext

from app.config import settings
from app.models.RPA_browser.browser_info_model import BaseFingerprintBrowserInitParams
from app.utils.consts.browser_exe_info.browser_exec_info_utils import (
    browser_exec_info_helper,
)
from botright.botright import Botright


class BaseUndetectedPlaywright:
    default_args: list[str]
    _base_user_data_dir: os.PathLike[str]

    @property
    def base_user_data_dir(self):
        return self._base_user_data_dir

    @property
    def user_data_dir(self):
        return self._user_data_dir

    def __init__(
        self,
        browser_token: uuid.UUID,
        browser_id: int,
        *,
        headless: bool = True,
    ):
        """
        headless测试的时候设置成False
        """

        self.default_args=[]
        self._base_user_data_dir = Path(
            os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "..", "user_data_dir"
            )
        )
        self.browser_token = browser_token
        self.browser_id = browser_id  # 如果没有提供browser_id，则生成一个
        self.headless = headless
        # 创建用户目录结构: browser_token/browser_id
        user_dir = os.path.join(self.base_user_data_dir, str(self.browser_token))
        os.makedirs(user_dir, exist_ok=True)  # 确保用户目录存在
        self._user_data_dir = os.path.join(user_dir, str(self.browser_id))

    async def launch_browser_span(
        self, fingerprint_params: BaseFingerprintBrowserInitParams
    ) -> AsyncGenerator[BrowserContext, Any]:
        """
        启动浏览器会话

        Args:
            fingerprint_params: 浏览器指纹参数，如果为None则使用默认参数
        """
        if fingerprint_params:
            self.default_args.extend(fingerprint_params.fp_2_args_list())
        browser_exec_info = await browser_exec_info_helper.get_exec_info(
            ua=fingerprint_params.patchright_browser_ua
        )
        botright_instance = await Botright(
            headless=self.headless,
            block_images=False,
            user_action_layer=False,
            fingerprint=fingerprint_params.browserforge_fingerprint_object,
            execute_path=browser_exec_info.exec_path,
        )
        botright_instance.flags.extend(self.default_args)
        browser = await botright_instance.new_browser(
            proxy=fingerprint_params.proxy_server,
            user_data_dir=self._user_data_dir,
            viewport=fingerprint_params.viewport,
            screen=fingerprint_params.screen,
        )
        # async with async_playwright() as playwright:
        #     browser = await playwright.chromium.launch_persistent_context(
        #         user_data_dir=self._user_data_dir,
        #         headless=self.headless,
        #         executable_path=settings.chromium_executable_path or None,
        #         args=self.default_args,
        #         viewport=fingerprint_params.viewport,
        #         screen=fingerprint_params.screen,
        #     )
        yield browser
        await browser.close()
