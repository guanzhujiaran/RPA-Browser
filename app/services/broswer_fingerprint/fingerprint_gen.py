import random
from dataclasses import asdict

from app.models.core.browser.fingerprint import (
    BaseFingerprintBrowserInitParams,
    PlatformEnum,
    BrowserEnum,
    MAX_DEVICE_SCALE_FACTOR,
    MIN_DEVICE_SCALE_FACTOR,
)
from app.models.runtime.api import BrowserFingerprintCreateParams
from app.models.database.browser.info import UserBrowserServerSideDefaultSetting
from app.services.broswer_fingerprint.exec_version import (
    resolve_exec_info,
    sync_ua_ch_data,
)
from app.utils.consts.browser_exe_info.browser_exec_info_utils import (
    browser_exec_info_helper,
)
from app.utils.http.proxy_utils import normalize_proxy_server
from app.utils.http.rand_headers_gen import (
    desktop_fingerprint_generator,
    mobile_fingerprint_generator,
)
from browserforge.fingerprints import Fingerprint
from loguru import logger


def _generate_fingerprint(ua: str, *, is_desktop: bool) -> Fingerprint:
    """按指定 UA 生成指纹；browserforge 数据表滞后时退化为不约束 UA。

    browserforge 的 header / 版本表跟随它的发布节奏，新内核（如 Chrome 150）可能
    还查不到对应 header 数据，此时 ``generate(user_agent=...)`` 会直接抛 ValueError。
    退化生成后仍由调用方把 UA / UA-CH / 版本号统一改写为清单值，
    指纹的其余部分（屏幕、字体、WebGL 等）与 UA 无关，不受影响。
    """
    generator = (
        desktop_fingerprint_generator if is_desktop else mobile_fingerprint_generator
    )
    try:
        return generator.generate(user_agent=[ua])
    except ValueError:
        logger.warning(
            f"browserforge 暂不支持该 UA 的 header 数据，退化为不约束 UA 生成: {ua}"
        )
        return generator.generate()


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
    if (
        platform == PlatformEnum.linux
        and "Windows" in rand_fingerprint.navigator.userAgent
    ):
        platform = PlatformEnum.windows
    elif (
        platform == PlatformEnum.linux and "Mac" in rand_fingerprint.navigator.userAgent
    ):
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
    user_default_settings: UserBrowserServerSideDefaultSetting | None,
    default_browser_setting: UserBrowserServerSideDefaultSetting,
    rand_fingerprint: Fingerprint,
) -> dict:
    """应用用户默认设置，返回配置字典"""
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
    # 代理只认用户显式配置的默认设置，绝不把「项目默认代理」（settings.default_proxy_server）
    # 写进指纹：它随环境变量变化，落库后就固化了。未显式配置时存 None，
    # 真正生效的兜底在启动时做（app/utils/http/proxy_utils.resolve_browser_proxy_server）。
    # 归一化（去 http:// 协议前缀）与启动路径保持同一口径，避免空串/带协议的值落库。
    proxy_server = normalize_proxy_server(
        user_default_settings and user_default_settings.default_proxy_server
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

    # 先定真实二进制，再用它的 UA 生成指纹：保证「真实版本 / 模拟版本 / UA」同源。
    # 指定版本必须先在清单里校验，非法直接 400（不允许生成一个没有二进制的版本）。
    resolved = await resolve_exec_info(params.fingerprint_real_version, None)
    exec_info = (
        resolved.exec_info
        if resolved is not None
        else await browser_exec_info_helper.pick_random_exec_info()
    )
    rand_fingerprint = _generate_fingerprint(
        exec_info.browser_ua, is_desktop=params.is_desktop
    )
    # UA 以清单为准（退化路径下 browserforge 给的是别的版本）
    rand_fingerprint.navigator.userAgent = exec_info.browser_ua
    # browserforge 的 languages 是从 Accept-Language 头按逗号拆出来的，带前导空格
    # （['zh-CN', ' zh', ' en', ...]）：不清理会既让 --accept-lang 出现非法语言项，
    # 也让落库的 fingerprint_dict 与 navigator.languages 不一致。
    rand_fingerprint.navigator.languages = [
        language.strip()
        for language in rand_fingerprint.navigator.languages or []
        if language.strip()
    ]
    # browserforge 的 uaFullVersion 来自它自己的版本表（如 139.0.7258.139），
    # 与清单二进制的 patch 号不同；这里以清单为准强制覆盖。
    if isinstance(rand_fingerprint.navigator.userAgentData, dict):
        rand_fingerprint.navigator.userAgentData = sync_ua_ch_data(
            rand_fingerprint.navigator.userAgentData, exec_info
        )
    # DPR 不沿用 browserforge 的离散值：真实浏览器可以缩放，DPR 会落在
    # [0.5, 1.75] 内的任意小数（系统缩放 × 页面缩放）。这里随机取两位小数，
    # 避免所有指纹都集中在 1 / 1.25 / 1.5 这几个固定值上。
    rand_fingerprint.screen.devicePixelRatio = round(
        random.uniform(MIN_DEVICE_SCALE_FACTOR, MAX_DEVICE_SCALE_FACTOR), 2
    )

    # 获取平台和浏览器信息
    platform = _map_platform_from_fingerprint(rand_fingerprint)
    ua_string = rand_fingerprint.navigator.userAgent
    brand = _detect_browser_brand(ua_string)
    # 模拟版本恒等于真实版本（清单 full_version），不从 browserforge 的版本表取值
    brand_version = exec_info.full_version
    platform_version = rand_fingerprint.navigator.userAgentData.get("platformVersion")

    # 应用用户设置
    user_settings = _apply_user_settings(
        user_default_settings, default_browser_setting, rand_fingerprint
    )

    browser_init_params = BaseFingerprintBrowserInitParams.model_construct(
        # 种子：显式传入就用（用户指定），否则随机。
        # 种子是内核伪装的根（canvas/audio/clientRects/字体等都从它派生），
        # 同一种子 = 同一台「虚拟设备」，所以创建时给值必须生效，不能一律随机。
        fingerprint=(
            params.fingerprint_int
            if params.fingerprint_int is not None
            else random.randint(-2147483648, 2147483647)
        ),
        fingerprint_platform=platform,
        fingerprint_platform_version=platform_version,
        fingerprint_browser=brand,
        fingerprint_brand_version=brand_version,
        fingerprint_real_version=exec_info.full_version,
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
    # 用户视口是「上限」而不是精确值：headful 窗口外框会额外占 8×131，
    # 视口必须收敛到 screen 内，否则 outerWidth/Height 会大于 screen，露馅。
    # 这里复用模型上的 viewport 计算（与启动时同一口径），保证落库值就是实际生效值。
    browser_init_params.patchright_viewport_width = browser_init_params.viewport["width"]
    browser_init_params.patchright_viewport_height = browser_init_params.viewport["height"]
    return browser_init_params
