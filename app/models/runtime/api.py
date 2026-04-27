"""
Runtime 模块 - 浏览器 API 模型

定义浏览器指纹和操作的 API 请求/响应模型。
"""

from typing import List

from pydantic import computed_field, field_validator
from sqlmodel import SQLModel

from app.models.base.base_sqlmodel import BasePaginationReq
from app.models.core.browser.fingerprint import Int32
from app.models.core.browser.info import UserBrowserInfoWithoutPlugin
from botright.modules.proxy_manager import ProxyManager, SplitError


# ========================
# 浏览器指纹相关请求参数
# ========================


class BrowserFingerprintCreateParams(SQLModel):
    """创建浏览器指纹参数"""

    fingerprint_int: Int32 | None = None
    is_desktop: bool = True


class BrowserFingerprintUpdateParams(SQLModel):
    """更新浏览器指纹参数"""

    id: int | str
    fingerprint_int: Int32 | None = None
    fingerprint_platform: str | None = None
    fingerprint_platform_version: str | None = None
    fingerprint_browser: str | None = None
    fingerprint_brand_version: str | None = None
    fingerprint_hardware_concurrency: int | None = None
    fingerprint_gpu_vendor: str | None = None
    fingerprint_gpu_renderer: str | None = None
    lang: str | None = None
    accept_lang: str | None = None
    timezone: str | None = None
    proxy_server: str | None = None
    custom_name: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v):
        if isinstance(v, str):
            return int(v)
        return v


class BrowserFingerprintUpsertParams(SQLModel):
    """创建或更新浏览器指纹参数 (upsert)"""

    id: int | str | None = None
    fingerprint_int: Int32 | None = None
    fingerprint_platform: str | None = None
    fingerprint_platform_version: str | None = None
    fingerprint_browser: str | None = None
    fingerprint_brand_version: str | None = None
    fingerprint_hardware_concurrency: int | None = None
    fingerprint_gpu_vendor: str | None = None
    fingerprint_gpu_renderer: str | None = None
    lang: str | None = None
    accept_lang: str | None = None
    timezone: str | None = None
    proxy_server: str | None = None
    custom_name: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v):
        if v is not None and isinstance(v, str):
            return int(v)
        return v

    @field_validator("proxy_server", mode="before")
    @classmethod
    def validate_proxy_server(cls, v: str):
        if v is not None:
            split_proxy = v.split(":")
            if len(split_proxy) == 2:
                pass
            elif len(split_proxy) == 3:
                if "@" in v:
                    helper = [_.split(":") for _ in v.split("@")]
                    split_proxy = [x for y in helper for x in y]
                    cls.split_helper(split_proxy, v)
                else:
                    raise SplitError(f"Proxy Format ({v}) isnt supported")
            elif len(split_proxy) == 4:
                cls.split_helper(split_proxy, v)
            else:
                raise SplitError(f"Proxy Format ({v}) isnt supported")
        return v

    @classmethod
    def split_helper(cls, split_proxy: List[str], proxy: str) -> None:
        """
        Helper function to split and parse the proxy string into its components.

        Args:
            split_proxy (List[str]): A list containing the components of the proxy string.
        """
        if not any([_.isdigit() for _ in split_proxy]):
            raise SplitError("No ProxyPort could be detected")
        if split_proxy[1].isdigit():
            pass
        elif split_proxy[3].isdigit():
            pass
        else:
            raise SplitError(f"Proxy Format ({proxy}) isnt supported")


class BrowserFingerprintQueryParams(SQLModel):
    """查询浏览器指纹参数"""

    id: int | str

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v):
        if isinstance(v, str):
            return int(v)
        return v


class BrowserFingerprintDeleteParams(SQLModel):
    """删除浏览器指纹参数"""

    id: int | str

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v):
        if isinstance(v, str):
            return int(v)
        return v


class BrowserFingerprintListParams(BasePaginationReq):
    """分页查询浏览器指纹参数"""

    pass


class BrowserFingerprintRenameParams(SQLModel):
    """重命名浏览器指纹参数"""

    id: int | str
    custom_name: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v):
        if isinstance(v, str):
            return int(v)
        return v


# ========================
# 浏览器指纹相关响应
# ========================


class BrowserFingerprintCreateResp(SQLModel):
    """创建浏览器指纹响应"""

    mid: int
    id: int

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)

    @computed_field
    @property
    def id_str(self) -> str:
        return str(self.id)


class BrowserFingerprintUpdateResp(SQLModel):
    """更新浏览器指纹响应"""

    mid: int
    id: int
    is_success: bool = True

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)

    @computed_field
    @property
    def id_str(self) -> str:
        return str(self.id)


class BrowserFingerprintDeleteResp(SQLModel):
    """删除浏览器指纹响应"""

    mid: int
    id: int
    is_success: bool = True

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)

    @computed_field
    @property
    def id_str(self) -> str:
        return str(self.id)


class BrowserFingerprintRenameResp(SQLModel):
    """重命名浏览器指纹响应"""

    mid: int
    id: int
    custom_name: str | None = None
    is_success: bool = True

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)

    @computed_field
    @property
    def id_str(self) -> str:
        return str(self.id)


class BrowserFingerprintQueryResp(UserBrowserInfoWithoutPlugin):
    """查询浏览器指纹响应"""

    pass


# ========================
# 浏览器操作相关请求参数
# ========================


class BrowserOperationOpenUrlParams(SQLModel):
    """打开浏览器URL参数"""

    browser_id: int | str
    url: str
    headless: bool = False

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_browser_id(cls, v):
        if isinstance(v, str):
            return int(v)
        return v


class BrowserOperationScreenshotParams(SQLModel):
    """浏览器截图参数"""

    browser_id: int | str
    full_page: bool = True
    headless: bool = False
    image_type: str | None = "png"

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_browser_id(cls, v):
        if isinstance(v, str):
            return int(v)
        return v


class BrowserOperationReleaseParams(SQLModel):
    """释放浏览器参数"""

    browser_id: int | str

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_browser_id(cls, v):
        if isinstance(v, str):
            return int(v)
        return v


# ========================
# 浏览器操作相关响应
# ========================


class BrowserOperationOpenUrlResp(SQLModel):
    """打开浏览器URL响应"""

    title: str | None = None
    current_url: str


class BrowserOperationScreenshotResp(SQLModel):
    """浏览器截图响应"""

    image_base64: str


class BrowserOperationReleaseResp(SQLModel):
    """释放浏览器响应"""

    mid: int
    browser_id: int
    is_success: bool = True

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)

    @computed_field
    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)


# ========================
# 向后兼容的别名
# ========================


UserBrowserInfoCreateParams = BrowserFingerprintCreateParams
UserBrowserInfoUpsertParams = BrowserFingerprintUpsertParams
UserBrowserInfoCreateResp = BrowserFingerprintCreateResp
UserBrowserInfoCountParams = SQLModel
UserBrowserInfoListParams = BrowserFingerprintListParams
UserBrowserInfoReadParams = BrowserFingerprintQueryParams
UserBrowserInfoReadResp = BrowserFingerprintQueryResp
UserBrowserInfoUpdateParams = BrowserFingerprintUpdateParams
UserBrowserInfoUpdateResp = BrowserFingerprintUpdateResp
UserBrowserInfoDeleteParams = BrowserFingerprintDeleteParams
UserBrowserInfoDeleteResp = BrowserFingerprintDeleteResp
BrowserOpenUrlParams = BrowserOperationOpenUrlParams
BrowserOpenUrlResp = BrowserOperationOpenUrlResp
BrowserScreenshotParams = BrowserOperationScreenshotParams
BrowserScreenshotResp = BrowserOperationScreenshotResp
BrowserReleaseParams = BrowserOperationReleaseParams
BrowserReleaseResp = BrowserOperationReleaseResp


__all__ = [
    # Fingerprint Params
    "BrowserFingerprintCreateParams",
    "BrowserFingerprintUpdateParams",
    "BrowserFingerprintUpsertParams",
    "BrowserFingerprintQueryParams",
    "BrowserFingerprintDeleteParams",
    "BrowserFingerprintListParams",
    "BrowserFingerprintRenameParams",
    # Fingerprint Resp
    "BrowserFingerprintCreateResp",
    "BrowserFingerprintUpdateResp",
    "BrowserFingerprintDeleteResp",
    "BrowserFingerprintRenameResp",
    "BrowserFingerprintQueryResp",
    # Operation Params
    "BrowserOperationOpenUrlParams",
    "BrowserOperationScreenshotParams",
    "BrowserOperationReleaseParams",
    # Operation Resp
    "BrowserOperationOpenUrlResp",
    "BrowserOperationScreenshotResp",
    "BrowserOperationReleaseResp",
    # Aliases
    "UserBrowserInfoCreateParams",
    "UserBrowserInfoUpsertParams",
    "UserBrowserInfoCreateResp",
    "UserBrowserInfoCountParams",
    "UserBrowserInfoListParams",
    "UserBrowserInfoReadParams",
    "UserBrowserInfoReadResp",
    "UserBrowserInfoUpdateParams",
    "UserBrowserInfoUpdateResp",
    "UserBrowserInfoDeleteParams",
    "UserBrowserInfoDeleteResp",
    "BrowserOpenUrlParams",
    "BrowserOpenUrlResp",
    "BrowserScreenshotParams",
    "BrowserScreenshotResp",
    "BrowserReleaseParams",
    "BrowserReleaseResp",
]
