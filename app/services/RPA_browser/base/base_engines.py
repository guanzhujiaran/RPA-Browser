import os
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

from playwright.async_api import BrowserContext

from app.config import settings
from app.models.RPA_browser.browser_info_model import BaseFingerprintBrowserInitParams
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

    def __init__(self,
                 browser_token: uuid.UUID,
                 browser_id: int,
                 *,
                 headless: bool = True,
                 ):
        """
        headless测试的时候设置成False
        """

        self.default_args = ['--incognito', '--accept-lang=en-US', '--lang=en-US', '--no-pings', '--mute-audio',
                             '--no-first-run', '--no-default-browser-check', '--disable-cloud-import',
                             '--disable-gesture-typing', '--disable-offer-store-unmasked-wallet-cards',
                             '--disable-offer-upload-credit-cards', '--disable-print-preview', '--disable-voice-input',
                             '--disable-wake-on-wifi', '--disable-cookie-encryption', '--ignore-gpu-blocklist',
                             '--enable-async-dns', '--enable-simple-cache-backend', '--enable-tcp-fast-open',
                             '--prerender-from-omnibox=disabled', '--enable-web-bluetooth',
                             '--disable-features=AudioServiceOutOfProcess,IsolateOrigins,site-per-process,TranslateUI,BlinkGenPropertyTrees',
                             '--aggressive-cache-discard', '--disable-extensions', '--disable-ipc-flooding-protection',
                             '--disable-blink-features=AutomationControlled', '--test-type',
                             '--enable-features=NetworkService,NetworkServiceInProcess,TrustTokens,TrustTokensAlwaysAllowIssuance',
                             '--disable-component-extensions-with-background-pages',
                             '--disable-default-apps', '--disable-breakpad', '--disable-component-update',
                             '--disable-domain-reliability', '--disable-sync',
                             '--disable-client-side-phishing-detection',
                             '--disable-hang-monitor', '--disable-popup-blocking', '--disable-prompt-on-repost',
                             '--metrics-recording-only', '--safebrowsing-disable-auto-update', '--password-store=basic',
                             '--autoplay-policy=no-user-gesture-required', '--use-mock-keychain',
                             '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                             '--webrtc-ip-handling-policy=disable_non_proxied_udp', '--disable-session-crashed-bubble',
                             '--disable-crash-reporter', '--disable-dev-shm-usage', '--force-color-profile=srgb',
                             '--disable-translate', '--disable-background-networking',
                             '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
                             '--disable-infobars',
                             '--hide-scrollbars', '--disable-renderer-backgrounding', '--font-render-hinting=none',
                             '--disable-logging', '--enable-surface-synchronization',
                             '--run-all-compositor-stages-before-draw', '--disable-threaded-animation',
                             '--disable-threaded-scrolling', '--disable-checker-imaging',
                             '--disable-new-content-rendering-timeout', '--disable-image-animation-resync',
                             '--disable-partial-raster', '--blink-settings=primaryHoverType=2,availableHoverTypes=2,'
                                                         'primaryPointerType=4,availablePointerTypes=4',
                             '--disable-layer-tree-host-memory-pressure']
        self._base_user_data_dir = Path(
            os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'user_data_dir'))
        self.browser_token = browser_token
        self.browser_id = browser_id  # 如果没有提供browser_id，则生成一个
        self.headless = headless
        # 创建用户目录结构: browser_token/browser_id
        user_dir = os.path.join(self.base_user_data_dir, str(self.browser_token))
        os.makedirs(user_dir, exist_ok=True)  # 确保用户目录存在
        self._user_data_dir = os.path.join(user_dir, str(self.browser_id))

    async def launch_browser_span(self, fingerprint_params: BaseFingerprintBrowserInitParams | None = None) -> \
            AsyncGenerator[BrowserContext, Any]:
        """
        启动浏览器会话
        
        Args:
            fingerprint_params: 浏览器指纹参数，如果为None则使用默认参数
        """
        if fingerprint_params:
            self.default_args.extend(fingerprint_params.fp_2_args_list())
        botright_instance = await Botright(
            headless=self.headless,
            block_images=True,
            user_action_layer=True,
            fingerprint=fingerprint_params.browserforge_fingerprint_object,
            execute_path=settings.chromium_executable_path or None
        )
        botright_instance.flags.extend(self.default_args)
        browser = await botright_instance.new_browser(
            proxy=fingerprint_params.proxy_server,
            user_data_dir=self._user_data_dir,
            viewport=fingerprint_params.viewport,
            screen=fingerprint_params.screen,
            executable_path=settings.chromium_executable_path or None,
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
