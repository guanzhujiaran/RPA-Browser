import random
from dataclasses import asdict

from app.models.RPA_browser.browser_info_model import (
    BaseFingerprintBrowserInitParams,
    PlatformEnum,
    BrowserFingerprintCreateParams,
    UserBrowserServerSideDefaultSetting,
    BrowserEnum,
)
from app.utils.consts.browser_exe_info.browser_exec_info_utils import (
    browser_exec_info_helper,
)
from app.utils.http.rand_headers_gen import (
    desktop_fingerprint_generator,
    mobile_fingerprint_generator,
)


async def gen_from_browserforge_fingerprint(
    *,
    params: BrowserFingerprintCreateParams,
    user_default_settings: UserBrowserServerSideDefaultSetting | None = None,
) -> BaseFingerprintBrowserInitParams:
    default_browser_setting = UserBrowserServerSideDefaultSetting(mid=-1)

    ua_list = await browser_exec_info_helper.get_exec_info_ua_list()
    if params.is_desktop:
        rand_fingerprint = desktop_fingerprint_generator.generate(user_agent=ua_list)
    else:
        rand_fingerprint = mobile_fingerprint_generator.generate(user_agent=ua_list)
    # 直接使用真实平台信息，进行精确映射
    bf_fingerprint_hashmap = {
        "Win32": PlatformEnum.windows,
        "Windows": PlatformEnum.windows,
        "Win64": PlatformEnum.windows,
        "MacIntel": PlatformEnum.macos,
        "Macintosh": PlatformEnum.macos,
        "Linux x86_64": PlatformEnum.linux,
        "Linux": PlatformEnum.linux,
        "X11": PlatformEnum.linux,
    }

    # 获取真实平台信息
    real_platform = rand_fingerprint.navigator.platform
    platform = bf_fingerprint_hashmap.get(
        real_platform, PlatformEnum.linux
    )  # 默认Linux

    # 如果映射失败，尝试从user agent中推断
    if (
        platform == PlatformEnum.linux
        and "Windows" in rand_fingerprint.navigator.userAgent
    ):
        platform = PlatformEnum.windows
    elif (
        platform == PlatformEnum.linux and "Mac" in rand_fingerprint.navigator.userAgent
    ):
        platform = PlatformEnum.macos
    # 直接从真实指纹中获取浏览器信息，而不是随机选择
    ua_string = rand_fingerprint.navigator.userAgent
    if "Chrome" in ua_string and "Edg" not in ua_string:
        brand = BrowserEnum.chrome
    elif "Edg" in ua_string:
        brand = BrowserEnum.Edge
    elif "OPR" in ua_string or "Opera" in ua_string:
        brand = BrowserEnum.Opera
    elif "Vivaldi" in ua_string:
        brand = BrowserEnum.Vivaldi
    else:
        # 默认使用Chrome
        brand = BrowserEnum.chrome

    brand_version = rand_fingerprint.navigator.userAgentData.get("uaFullVersion")
    platform_version = rand_fingerprint.navigator.userAgentData.get("platformVersion")

    # 应用用户默认设置（如果存在）
    lang = (
        user_default_settings
        and user_default_settings.default_lang
        or default_browser_setting.default_lang
        or rand_fingerprint.navigator.language
    )
    timezone = (
        user_default_settings
        and user_default_settings.default_timezone
        or default_browser_setting.default_timezone
        or "Asia/Shanghai"
    )
    viewport_width = (
        user_default_settings
        and user_default_settings.default_viewport_width
        or default_browser_setting.default_viewport_width
        or rand_fingerprint.screen.availWidth
    )
    viewport_height = (
        user_default_settings
        and user_default_settings.default_viewport_height
        or default_browser_setting.default_viewport_height
        or rand_fingerprint.screen.availHeight
    )
    proxy_server = user_default_settings and user_default_settings.default_proxy_server or default_browser_setting.default_proxy_server or ""
    return BaseFingerprintBrowserInitParams.model_construct(
        fingerprint=random.randint(-2147483648, 2147483647),
        fingerprint_platform=platform,
        fingerprint_platform_version=platform_version,
        fingerprint_browser=brand,
        fingerprint_brand_version=brand_version,
        fingerprint_hardware_concurrency=rand_fingerprint.navigator.hardwareConcurrency,
        fingerprint_gpu_vendor=rand_fingerprint.videoCard.vendor,
        fingerprint_gpu_renderer=rand_fingerprint.videoCard.renderer,
        lang=lang,
        accept_lang=",".join(rand_fingerprint.navigator.languages),
        timezone=timezone,
        patchright_screen_width=rand_fingerprint.screen.width,
        patchright_screen_height=rand_fingerprint.screen.height,
        patchright_viewport_width=viewport_width,
        patchright_viewport_height=viewport_height,
        proxy_server=proxy_server,
        patchright_fingerprint_dict=asdict(rand_fingerprint),
        patchright_browser_ua=ua_string,
    )
