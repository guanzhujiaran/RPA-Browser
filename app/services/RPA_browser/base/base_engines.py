import os
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator
from playwright.async_api import BrowserContext
from pydantic import computed_field
from app.models.core.browser.fingerprint import BaseFingerprintBrowserInitParams
from app.utils.consts.browser_exe_info.browser_exec_info_utils import (
    browser_exec_info_helper,
)
from app.utils.http.proxy_utils import (
    resolve_browser_proxy_bypass,
    resolve_browser_proxy_server,
)
from app.config import CONF
from botright.botright import Botright
from loguru import logger


class BaseUndetectedPlaywright:
    default_args: list[str]
    _base_user_data_dir: os.PathLike[str]

    @computed_field
    @property
    def base_user_data_dir(self):
        return self._base_user_data_dir

    @computed_field
    @property
    def user_data_dir(self):
        return self._user_data_dir

    def __init__(
        self,
        mid: int,
        browser_id: int,
        *,
        headless: bool = False,
    ):
        """
        headless测试的时候设置成False
        """

        self.default_args = []
        self._base_user_data_dir = Path(CONF.Path.user_data_dir)
        self.mid = mid
        self.browser_id = browser_id  # 如果没有提供browser_id，则生成一个
        self.headless = headless
        # 创建用户目录结构: mid/browser_id
        user_dir = os.path.join(self.base_user_data_dir, str(self.mid))
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
            # 指纹未指定代理时回退到项目默认代理（settings.default_proxy_server），
            # 并归一化成 botright 可解析的 host:port 形式（配置里可能带 http:// 前缀）。
            # 代理只通过下面 botright 的 proxy 参数下发（driver 会自己拼 --proxy-server），
            # 不在默认参数里重复设置同名开关。
            fingerprint_params.proxy_server = resolve_browser_proxy_server(
                fingerprint_params.proxy_server
            )
            # 绕过列表同样支持「浏览器单独配置 → 服务端环境变量」两级解析；
            # 代理由上面的 proxy_server 唯一决定，没有代理时绕过列表无意义。
            fingerprint_params.proxy_bypass = resolve_browser_proxy_bypass(
                fingerprint_params.proxy_bypass
            )
            logger.debug(
                f"浏览器代理: {fingerprint_params.proxy_server or '未配置（直连）'}"
                f"，绕过: {fingerprint_params.proxy_bypass or '无'}"
            )
            self.default_args.extend(fingerprint_params.fp_2_args_list())
        browser_exec_info = await browser_exec_info_helper.get_exec_info(
            ua=fingerprint_params.patchright_browser_ua
        )
        botright_instance: Botright = await Botright(
            headless=self.headless,
            block_images=False,
            # user_action_layer 会在页面注入全屏 canvas，并监听每个 mousemove 画圆
            # （调试用可视化）。配合人性化轨迹的 100 点移动 = 每次移动几百次 canvas
            # 绘制，CPU 占用极高，已关闭（2.70.2）。
            user_action_layer=False,
            fingerprint=fingerprint_params.browserforge_fingerprint_object,
            execute_path=browser_exec_info.exec_path,
        )
        botright_instance.flags.extend(self.default_args)
        browser = await botright_instance.new_browser(
            proxy=fingerprint_params.proxy_server,
            proxy_bypass=fingerprint_params.proxy_bypass,
            user_data_dir=self._user_data_dir,
            viewport=fingerprint_params.viewport,
            screen=fingerprint_params.screen,
            accept_downloads=False,
        )
        await (
            browser.new_page()
        )  # 使用新的mock好的页面,直接调用new_page就行了,botright会自动处理关闭初始页面
        yield browser
        await browser.close()
