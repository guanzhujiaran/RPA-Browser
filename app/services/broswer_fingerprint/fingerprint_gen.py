import random
from dataclasses import asdict

from app.models.RPA_browser.browser_info_model import BaseFingerprintBrowserInitParams, PlatformEnum, \
    UserBrowserInfoCreateParams, \
    BrowserEnum
from app.utils.consts.browser_exe_info.browser_exec_info_utils import browser_exec_info_helper
from app.utils.http.rand_headers_gen import desktop_fingerprint_generator, mobile_fingerprint_generator


async def gen_from_browserforge_fingerprint(
        *,
        params: UserBrowserInfoCreateParams
) -> BaseFingerprintBrowserInitParams:
    if params.fingerprint_int:
        return BaseFingerprintBrowserInitParams(
            fingerprint=params.fingerprint_int
        )
    ua_list = await browser_exec_info_helper.get_exec_info_ua_list()
    if params.is_desktop:
        rand_fingerprint = desktop_fingerprint_generator.generate(user_agent=ua_list)
    else:
        rand_fingerprint = mobile_fingerprint_generator.generate(user_agent=ua_list)
    bf_fingerprint_hashmap = {
        'Win32': PlatformEnum.windows,
        'MacIntel': PlatformEnum.macos,
        'Linux x86_64': PlatformEnum.linux,
    }
    platform = bf_fingerprint_hashmap.get(rand_fingerprint.navigator.platform, PlatformEnum.windows)
    brand = random.choice(list(BrowserEnum))
    brand_version = rand_fingerprint.navigator.userAgentData.get('uaFullVersion')
    platform_version = rand_fingerprint.navigator.userAgentData.get('platformVersion')
    return BaseFingerprintBrowserInitParams(
        fingerprint=random.randint(-2147483648, 2147483647),
        fingerprint_platform=platform,
        fingerprint_platform_version=platform_version,
        fingerprint_browser=brand,
        fingerprint_brand_version=brand_version,
        fingerprint_hardware_concurrency=rand_fingerprint.navigator.hardwareConcurrency,
        fingerprint_gpu_vendor=rand_fingerprint.videoCard.vendor,
        fingerprint_gpu_renderer=rand_fingerprint.videoCard.renderer,
        lang=rand_fingerprint.navigator.language,
        accept_lang=','.join(rand_fingerprint.navigator.languages),
        patchright_screen_width=rand_fingerprint.screen.width,
        patchright_screen_height=rand_fingerprint.screen.height,
        patchright_viewport_width=rand_fingerprint.screen.availWidth,
        patchright_viewport_height=rand_fingerprint.screen.availHeight,
        patchright_fingerprint_dict=asdict(rand_fingerprint)
    )
