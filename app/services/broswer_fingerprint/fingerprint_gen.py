import random
from dataclasses import asdict
from typing import Optional

from app.models.core.browser.fingerprint import (
    BaseFingerprintBrowserInitParams,
    PlatformEnum,
    BrowserEnum,
)
from app.models.runtime.api import BrowserFingerprintCreateParams
from app.models.database.browser.info import UserBrowserServerSideDefaultSetting
from app.utils.consts.browser_exe_info.browser_exec_info_utils import (
    browser_exec_info_helper,
)
from app.utils.http.rand_headers_gen import (
    desktop_fingerprint_generator,
    mobile_fingerprint_generator,
)
from browserforge.fingerprints import Fingerprint

def _map_platform_from_fingerprint(rand_fingerprint: Fingerprint) -> PlatformEnum:
    """从指纹信息映射平台类型"""
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
    platform = bf_fingerprint_hashmap.get(real_platform, PlatformEnum.linux)

    # 如果映射失败，尝试从user agent中推断
    if platform == PlatformEnum.linux and "Windows" in rand_fingerprint.navigator.userAgent:
        platform = PlatformEnum.windows
    elif platform == PlatformEnum.linux and "Mac" in rand_fingerprint.navigator.userAgent:
        platform = PlatformEnum.macos
    
    return platform


def _detect_browser_brand(ua_string: str) -> BrowserEnum:
    """从User Agent字符串检测浏览器品牌"""
    if "Chrome" in ua_string and "Edg" not in ua_string:
        return BrowserEnum.chrome
    elif "Edg" in ua_string:
        return BrowserEnum.Edge
    elif "OPR" in ua_string or "Opera" in ua_string:
        return BrowserEnum.Opera
    elif "Vivaldi" in ua_string:
        return BrowserEnum.Vivaldi
    else:
        # 默认使用Chrome
        return BrowserEnum.chrome


def _apply_user_settings(
    user_default_settings: Optional[UserBrowserServerSideDefaultSetting],
    default_browser_setting: UserBrowserServerSideDefaultSetting,
    rand_fingerprint: Fingerprint,
) -> dict:
    """应用用户默认设置，返回配置字典"""
    lang = (
        user_default_settings and user_default_settings.default_lang
        or default_browser_setting.default_lang
        or rand_fingerprint.navigator.language
    )
    timezone = (
        user_default_settings and user_default_settings.default_timezone
        or default_browser_setting.default_timezone
        or "Asia/Shanghai"
    )
    viewport_width = (
        user_default_settings and user_default_settings.default_viewport_width
        or default_browser_setting.default_viewport_width
        or rand_fingerprint.screen.availWidth
    )
    viewport_height = (
        user_default_settings and user_default_settings.default_viewport_height
        or default_browser_setting.default_viewport_height
        or rand_fingerprint.screen.availHeight
    )
    proxy_server = (
        user_default_settings and user_default_settings.default_proxy_server
        or default_browser_setting.default_proxy_server
        or ""
    )
    
    return {
        "lang": lang,
        "timezone": timezone,
        "viewport_width": viewport_width,
        "viewport_height": viewport_height,
        "proxy_server": proxy_server,
    }


async def gen_from_browserforge_fingerprint(
    *,
    params: BrowserFingerprintCreateParams,
    user_default_settings: UserBrowserServerSideDefaultSetting | None = None,
) -> BaseFingerprintBrowserInitParams:
    default_browser_setting = UserBrowserServerSideDefaultSetting(mid=-1)

    ua_list = await browser_exec_info_helper.get_exec_info_ua_list()
    rand_fingerprint = (
        desktop_fingerprint_generator.generate(user_agent=ua_list)
        if params.is_desktop
        else mobile_fingerprint_generator.generate(user_agent=ua_list)
    )

    # 获取平台和浏览器信息
    platform = _map_platform_from_fingerprint(rand_fingerprint)
    ua_string = rand_fingerprint.navigator.userAgent
    brand = _detect_browser_brand(ua_string)
    brand_version = rand_fingerprint.navigator.userAgentData.get("uaFullVersion")
    platform_version = rand_fingerprint.navigator.userAgentData.get("platformVersion")

    # 应用用户设置
    user_settings = _apply_user_settings(
        user_default_settings, default_browser_setting, rand_fingerprint
    )

    return BaseFingerprintBrowserInitParams.model_construct(
        fingerprint=random.randint(-2147483648, 2147483647),
        fingerprint_platform=platform,
        fingerprint_platform_version=platform_version,
        fingerprint_browser=brand,
        fingerprint_brand_version=brand_version,
        fingerprint_hardware_concurrency=rand_fingerprint.navigator.hardwareConcurrency,
        fingerprint_gpu_vendor=rand_fingerprint.videoCard.vendor,
        fingerprint_gpu_renderer=rand_fingerprint.videoCard.renderer,
        lang=user_settings["lang"],
        accept_lang=",".join(rand_fingerprint.navigator.languages),
        timezone=user_settings["timezone"],
        patchright_screen_width=rand_fingerprint.screen.width,
        patchright_screen_height=rand_fingerprint.screen.height,
        patchright_viewport_width=user_settings["viewport_width"],
        patchright_viewport_height=user_settings["viewport_height"],
        proxy_server=user_settings["proxy_server"],
        patchright_fingerprint_dict=asdict(rand_fingerprint),
        patchright_browser_ua=ua_string,
    )
