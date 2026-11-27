"""
Core 模块 - 浏览器指纹模型

定义浏览器指纹相关的枚举和参数模型。
"""

from bili_common.models import StrEnumAutoDoc
from typing import Annotated, ClassVar
from sqlalchemy import Text
from sqlmodel import Field, SQLModel, Column, JSON
from browserforge.fingerprints import (
    Fingerprint,
    ScreenFingerprint,
    NavigatorFingerprint,
    VideoCard,
)
from dacite import from_dict
from playwright.async_api import ViewportSize
from pydantic import model_validator, computed_field, field_validator
import sys

Int32 = Annotated[int, Field(ge=-2147483648, le=2147483647)]

# headful Chromium 的窗口外框（viewport 之外的部分）：Playwright 在 Linux 下按
# 宽 8 / 高 85 的 insets 设置窗口，persistent context 高度再额外加 46，合计 8×131。
# 窗口外框必须落在 screen 内，否则 window.outerWidth/Height 会大于
# screen.width/height——这是物理上不可能的组合，指纹检测会直接看穿。
WINDOW_FRAME_WIDTH = 8
WINDOW_FRAME_HEIGHT = 131

# DPR 可接受区间：2 / 2.5 / 3 这类高分屏组合会按倍数放大渲染与截图像素，
# 且在桌面真机分布里占比很低，统一收敛到 [0.5, 1.75]。
MIN_DEVICE_SCALE_FACTOR = 0.5
MAX_DEVICE_SCALE_FACTOR = 1.75

# `--fingerprint-brand` 认的是文档里的字面量（首字母大写），而库里存的是
# ENUM 值（chrome / Edge / Opera / Vivaldi）。下发命令行前统一映射一次。
BROWSER_BRAND_ARG_VALUES: dict[str, str] = {
    "chrome": "Chrome",
    "edge": "Edge",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
}

#: `--fingerprint-gpu-vendor` / `--fingerprint-gpu-renderer` 在 Chrome 144 被内核移除
#: （替代方案是 ``--disable-spoofing=gpu``，不是反方向指定）。因此 144+ 上这两个字段
#: 只是被记录下来，实际 GPU 由 ``--fingerprint`` 的种子派生，UI 需要标注这一点。
GPU_OVERRIDE_LAST_SUPPORTED_MAJOR = 143


def clamp_device_scale_factor(value: float) -> float:
    """把 DPR 收敛到可接受区间。"""
    return min(max(float(value), MIN_DEVICE_SCALE_FACTOR), MAX_DEVICE_SCALE_FACTOR)


def clamp_viewport_to_screen(
    viewport_width: int,
    viewport_height: int,
    screen_width: int,
    screen_height: int,
) -> tuple[int, int]:
    """把 viewport 收敛到屏幕内，保证 headful 窗口外框不会超出 screen。"""
    return (
        max(1, min(viewport_width, screen_width - WINDOW_FRAME_WIDTH)),
        max(1, min(viewport_height, screen_height - WINDOW_FRAME_HEIGHT)),
    )


class PlatformEnum(StrEnumAutoDoc):
    """操作系统平台枚举"""

    windows = "windows"
    linux = "linux"
    macos = "macos"


class BrowserEnum(StrEnumAutoDoc):
    """浏览器类型枚举"""

    chrome = "chrome"
    Edge = "Edge"
    Opera = "Opera"
    Vivaldi = "Vivaldi"


class LogPluginLogLevelEnum(StrEnumAutoDoc):
    """日志插件日志级别枚举"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class BaseBrowserId(SQLModel):
    """
    浏览器指纹基础参数模型
    """

    browser_id: int | str

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_id(cls, v):
        return int(v) if v is not None and isinstance(v, str) else v

    @computed_field
    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)


class BaseBrowserIdOptional(SQLModel):
    """
    浏览器指纹基础参数模型
    """

    browser_id: int | str | None = None

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_id(cls, v):
        return int(v) if v is not None and isinstance(v, str) else v

    @computed_field
    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id or "")


class BaseUserMid(SQLModel):
    """
    用户ID基础参数模型
    """

    mid: int | str

    @field_validator("mid", mode="before")
    @classmethod
    def validate_id(cls, v):
        return int(v) if v is not None and isinstance(v, str) else v

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)


class BaseFeedbackInfo(SQLModel):
    is_success: bool = True


class BaseFingerprintBrowserInitParams(SQLModel):
    """
    浏览器指纹基础参数模型
    存放可反射的浏览器的启动args
    """

    fingerprint: Int32 = Field(None, unique=True, alias="--fingerprint")
    fingerprint_platform: PlatformEnum | None = Field(
        PlatformEnum.windows,
        alias="--fingerprint-platform",
    )
    fingerprint_platform_version: str | None = Field(
        None,
        alias="--fingerprint-platform-version",
        description="Uses default version if not specified",
    )
    # 上游 fingerprint-chromium 的开关名是 --fingerprint-brand（曾误写成
    # --fingerprint-browser，Chromium 会静默忽略未知开关，等于品牌从未下发）。
    fingerprint_browser: BrowserEnum | None = Field(
        None,
        alias="--fingerprint-brand",
        description="Chrome, Edge, Opera, Vivaldi (default is Chrome)",
    )
    fingerprint_brand_version: str | None = Field(
        None,
        alias="--fingerprint-brand-version",
        description="Uses default version if not specified",
    )
    fingerprint_hardware_concurrency: int | None = Field(
        None, alias="--fingerprint-hardware-concurrency"
    )
    fingerprint_gpu_vendor: str | None = Field(
        None,
        alias="--fingerprint-gpu-vendor",
        description=f"Vendor string (e.g., Intel Inc., NVIDIA Corporation). "
        f"仅 Chrome {GPU_OVERRIDE_LAST_SUPPORTED_MAJOR} 及以下内核生效"
        f"（{GPU_OVERRIDE_LAST_SUPPORTED_MAJOR + 1} 起该开关已被移除，实际 GPU 由指纹种子决定）",
    )
    fingerprint_gpu_renderer: str | None = Field(
        None,
        alias="--fingerprint-gpu-renderer",
        description=f"Renderer string (e.g., Intel Iris OpenGL Engine, NVIDIA GeForce GTX 1060). "
        f"仅 Chrome {GPU_OVERRIDE_LAST_SUPPORTED_MAJOR} 及以下内核生效"
        f"（{GPU_OVERRIDE_LAST_SUPPORTED_MAJOR + 1} 起该开关已被移除，实际 GPU 由指纹种子决定）",
    )
    lang: str | None = Field(
        None, alias="--lang", description="Language code (e.g., en-US)"
    )
    accept_lang: str | None = Field(
        None, alias="--accept-lang", description="Language code (e.g., en-US)"
    )
    timezone: str | None = Field(
        "Asia/Shanghai",
        alias="--timezone",
        description="Timezone (e.g., America/New_York)",
    )
    proxy_server: str | None = Field(
        None,
        alias="--proxy-server",
        description="http, socks proxy (password authentication not supported)",
    )
    # 绕过规则数量不设上限，用 TEXT 而不是默认的 VARCHAR(255)
    proxy_bypass: str | None = Field(
        None,
        sa_type=Text,
        description="代理绕过列表：逗号分隔的 host 规则（如 *.bilibili.com,127.0.0.1），"
        "命中的主机直连、不走 proxy_server。只能匹配主机名/IP，不能写完整 URL；"
        "未配置时回退到服务端环境变量 browser_proxy_bypass",
    )
    patchright_screen_width: int = Field(default=1920)
    patchright_screen_height: int = Field(default=1080)
    patchright_viewport_width: int = Field(default=1920)
    patchright_viewport_height: int = Field(default=1080)
    fingerprint_real_version: str | None = Field(
        None,
        nullable=True,
        description="真实浏览器版本（来自 browser_exec_info.json，决定实际启动的 chromium 二进制）；"
        "模拟版本 fingerprint_brand_version 由它派生",
    )
    patchright_fingerprint_dict: dict | None = Field(
        default=None, sa_column=Column(JSON)
    )
    patchright_browser_ua: str | None = Field(None, nullable=True)

    model_config = {"populate_by_name": True}

    @property
    def browserforge_fingerprint_object(self) -> Fingerprint | None:
        """生成BrowserForge指纹对象"""
        if not self.patchright_fingerprint_dict:
            return None
        screen_fingerprint = from_dict(
            ScreenFingerprint, self.patchright_fingerprint_dict.get("screen")
        )
        navigator_fingerprint = NavigatorFingerprint(
            **self.patchright_fingerprint_dict.get("navigator")
        )
        navigator_fingerprint.userAgent = (
            self.patchright_browser_ua or navigator_fingerprint.userAgent
        )
        video_card = from_dict(
            VideoCard, self.patchright_fingerprint_dict.get("videoCard")
        )
        return Fingerprint(
            screen=screen_fingerprint,
            navigator=navigator_fingerprint,
            headers=self.patchright_fingerprint_dict.get("headers"),
            videoCodecs=self.patchright_fingerprint_dict.get("videoCodecs"),
            audioCodecs=self.patchright_fingerprint_dict.get("audioCodecs"),
            pluginsData=self.patchright_fingerprint_dict.get("pluginsData"),
            battery=self.patchright_fingerprint_dict.get("battery"),
            videoCard=video_card,
            multimediaDevices=self.patchright_fingerprint_dict.get("multimediaDevices"),
            fonts=self.patchright_fingerprint_dict.get("fonts"),
            mockWebRTC=self.patchright_fingerprint_dict.get("mockWebRTC"),
            slim=self.patchright_fingerprint_dict.get("slim"),
        )

    @computed_field
    @property
    def viewport(self) -> ViewportSize:
        """浏览器视口大小（收敛到屏幕内，避免窗口外框超出 screen）"""
        width, height = clamp_viewport_to_screen(
            self.patchright_viewport_width,
            self.patchright_viewport_height,
            self.patchright_screen_width,
            self.patchright_screen_height,
        )
        return {"width": width, "height": height}

    @property
    def device_scale_factor(self) -> float:
        """指纹中的 DPR。

        Playwright 的 viewport 仿真在未显式传 device_scale_factor 时固定按 1 处理，
        会导致 screen（如 1536×864）与指纹里的 DPR（如 1.25）脱节，启动时必须下发。
        """
        fingerprint_dict = self.patchright_fingerprint_dict or {}
        screen_dict = fingerprint_dict.get("screen") or {}
        return clamp_device_scale_factor(screen_dict.get("devicePixelRatio") or 1.0)

    @computed_field
    @property
    def screen(self) -> ViewportSize:
        """屏幕大小"""
        return {
            "width": self.patchright_screen_width,
            "height": self.patchright_screen_height,
        }

    # 这些字段是给 playwright launch kwargs 用的（见 base_engines.launch_browser_span），
    # 不是 chromium 命令行开关，不能进入 args，否则会注入 --viewport={'width': ...} 这类非法参数。
    # proxy_server / proxy_bypass 同样如此：代理通过 playwright 的 proxy 参数唯一设置
    # （driver 会自己拼 --proxy-server/--proxy-bypass-list），这里再拼一份就是重复设置同一个开关。
    # 注意 key 是去掉 "--" 后的字段名（proxy_bypass 没有 alias，所以用下划线形式）。
    NON_CLI_ARG_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "viewport",
            "screen",
            "proxy-server",
            "proxy_bypass",
            # 真实版本只用于选二进制，chromium 没有对应命令行开关；
            # 模拟版本走 --fingerprint-brand-version
            "fingerprint_real_version",
        }
    )

    @property
    def fingerprint_brand_arg_value(self) -> str | None:
        """`--fingerprint-brand` 的命令行字面量。

        库里 `fingerprint_browser` 存的是 ENUM 值（如 ``chrome``），而内核认的是
        ``Chrome`` / ``Edge`` / ``Opera`` / ``Vivaldi``，大小写不匹配会被当成默认品牌。
        """
        brand = self.fingerprint_browser
        if brand is None:
            return None
        raw = str(getattr(brand, "value", brand)).strip()
        return BROWSER_BRAND_ARG_VALUES.get(raw.lower(), raw)

    def fp_2_args_list(self) -> list[str]:
        """将指纹参数转换为浏览器启动参数列表"""
        if not sys.platform.startswith(
            "linux"
        ):  # WebGL 元数据：修改 GPU 供应商和显卡型号（暂时只支持 Linux）
            self.fingerprint_gpu_vendor = None
            self.fingerprint_gpu_renderer = None
        filtered_params = {}
        for k, v in self.model_dump(exclude_none=True, by_alias=True).items():
            # 别名本身已带 "--" 前缀（如 --fingerprint-platform），需先去掉再判断
            normalized_key = k.lstrip("-")
            # 用「是否为 None / 空串」判断而不是 `not v`：种子 0 是合法值，
            # 被当成空值丢掉会让 --fingerprint 不下发（等于内核伪装全关）。
            if v is None or (isinstance(v, str) and not v):
                continue
            if normalized_key.startswith("patchright"):
                continue
            if normalized_key in self.NON_CLI_ARG_KEYS:
                continue
            if normalized_key == "fingerprint-brand":
                v = self.fingerprint_brand_arg_value or v
            filtered_params[normalized_key] = v
        return [f"--{k.replace('_', '-')}={v}" for k, v in filtered_params.items()]

    @model_validator(mode="after")
    def check_browser_and_brand_version_consistency(self):
        """验证浏览器类型和品牌版本的一致性"""
        browser = self.fingerprint_browser
        brand_version = self.fingerprint_brand_version

        if (browser is None) != (brand_version is None):
            raise ValueError(
                "fingerprint_browser and fingerprint_brand_version must be both set or both unset."
            )
        return self

    @model_validator(mode="after")
    def check_browser_vendor_and_renderer_consistency(self):
        """验证GPU供应商和渲染器的一致性"""
        gpu_vendor = self.fingerprint_gpu_vendor
        gpu_renderer = self.fingerprint_gpu_renderer
        if (gpu_vendor is None) != (gpu_renderer is None):
            raise ValueError(
                "fingerprint_gpu_vendor and fingerprint_gpu_renderer must be both set or both unset."
            )
        return self


__all__ = [
    "PlatformEnum",
    "BrowserEnum",
    "LogPluginLogLevelEnum",
    "Int32",
    "GPU_OVERRIDE_LAST_SUPPORTED_MAJOR",
    "BaseFingerprintBrowserInitParams",
    "BaseBrowserIdOptional",
    "BaseBrowserId",
    "BaseUserMid",
    "BaseFeedbackInfo",
]
