"""
Runtime 模块 - 浏览器 API 模型

定义浏览器指纹和操作的 API 请求/响应模型。
"""

from typing import List
from pydantic import field_validator
from sqlmodel import SQLModel
from app.models.base.base_sqlmodel import BasePaginationReq
from app.models.core.browser.fingerprint import Int32, BaseBrowserId,\
    BaseBrowserIdOptional, BaseUserMid, BaseFeedbackInfo, PlatformEnum, BrowserEnum
from app.models.database.browser.info import UserBrowserInfoWithoutPlugin
from botright.modules.proxy_manager import SplitError


# ========================
# 浏览器指纹相关请求参数
# ========================


class BrowserFingerprintCreateParams(SQLModel):
    """创建浏览器指纹参数"""

    fingerprint_int: Int32 | None = None
    is_desktop: bool = True


def _canonical_enum_value(value, enum_cls, field_name: str):
    """把平台/浏览器取值归一到大写敏感无关的枚举成员上。

    这两列在库里是 MySQL ENUM（platformenum / browserenum），写成非法值会在
    落库时抛 1265 Data truncated（HTTP 500）；这里提前归一/拦截：
    - 空值（None / '' / 全空格）→ None（视为「未提供」）；
    - 大小写不同的合法值（前端传 edge，枚举成员是 Edge）→ 归一为枚举成员字面量；
    - 其余非法值 → ValueError，由 FastAPI 统一转成 400 参数校验失败。
    """
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        for member in enum_cls:
            if member.value.lower() == value.strip().lower():
                return member.value
    raise ValueError(
        f"{field_name} 取值非法: {value!r}，可选值: {[m.value for m in enum_cls]}"
    )


class BrowserFingerprintEditableParams(SQLModel):
    """用户可编辑的浏览器指纹字段（update / upsert 共用）。

    统一做一层入参归一，避免前端表单的「未填写」值（空字符串）直接落库：
    - 空白字符串 → None（未提供）。ENUM 列写入 '' 会直接报 1265 Data truncated；
      同时空串也会把指纹生成器填好的默认值冲掉，故统一按「未提供」处理；
    - 平台/浏览器额外做大小写无关的枚举校验。
    服务层配合使用 exclude_none，即可保证「未提供」的字段不覆盖生成值/原有值。
    """

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

    @field_validator(
        "fingerprint_platform_version",
        "fingerprint_brand_version",
        "fingerprint_gpu_vendor",
        "fingerprint_gpu_renderer",
        "lang",
        "accept_lang",
        "timezone",
        "proxy_server",
        "custom_name",
        mode="before",
    )
    @classmethod
    def blank_string_to_none(cls, v):
        """空白字符串按「未提供」处理，确保不会以空字符串形态落库。"""
        if isinstance(v, str):
            return v if v.strip() else None
        return v

    @field_validator("fingerprint_platform", mode="before")
    @classmethod
    def normalize_platform(cls, v):
        return _canonical_enum_value(v, PlatformEnum, "fingerprint_platform")

    @field_validator("fingerprint_browser", mode="before")
    @classmethod
    def normalize_browser(cls, v):
        return _canonical_enum_value(v, BrowserEnum, "fingerprint_browser")


class BrowserFingerprintUpdateParams(BaseBrowserId, BrowserFingerprintEditableParams):
    """更新浏览器指纹参数"""


class BrowserFingerprintUpsertParams(BaseBrowserIdOptional, BrowserFingerprintEditableParams):
    """创建或更新浏览器指纹参数 (upsert)"""

    @field_validator("proxy_server", mode="before")
    @classmethod
    def validate_proxy_server(cls, v: str):
        if v is not None and v.strip():
            split_proxy = v.split(":")
            if len(split_proxy) == 2:
                ...
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
        if not any(_.isdigit() for _ in split_proxy):
            raise SplitError("No ProxyPort could be detected")
        elif not split_proxy[3].isdigit():
            ...
        elif not split_proxy[3].isdigit():
            raise SplitError(f"Proxy Format ({proxy}) isnt supported")


class BrowserFingerprintQueryParams(BaseBrowserId):
    """查询浏览器指纹参数"""
    ...


class BrowserFingerprintDeleteParams(BaseBrowserId):
    """删除浏览器指纹参数"""

    ...


class BrowserFingerprintListParams(BasePaginationReq):
    """分页查询浏览器指纹参数"""

    ...


class BrowserFingerprintRenameParams(SQLModel):
    """重命名浏览器指纹参数"""
    custom_name: str | None = None

# ========================
# 浏览器指纹相关响应
# ========================


class BrowserFingerprintCreateResp(BaseUserMid,BaseBrowserId):
    """创建浏览器指纹响应"""
    ...


class BrowserFingerprintUpdateResp(BaseUserMid,BaseBrowserId,BaseFeedbackInfo):
    """更新浏览器指纹响应"""
    ...

class BrowserFingerprintDeleteResp(BaseUserMid,BaseBrowserId,BaseFeedbackInfo):
    """删除浏览器指纹响应"""
    ...
class BrowserFingerprintRenameResp(BaseUserMid,BaseBrowserId,BaseFeedbackInfo):
    """重命名浏览器指纹响应"""
    custom_name: str | None = None


class BrowserFingerprintQueryResp(UserBrowserInfoWithoutPlugin):
    """查询浏览器指纹响应"""
    ...


# ========================
# 浏览器操作相关请求参数
# ========================


class BrowserOperationOpenUrlParams(BaseBrowserId):
    """打开浏览器URL参数"""
    url: str
    headless: bool = False


class BrowserOperationScreenshotParams(BaseBrowserId):
    """浏览器截图参数"""
    full_page: bool = True
    headless: bool = False
    image_type: str | None = "png"


class BrowserOperationReleaseParams(BaseBrowserId):
    """释放浏览器参数"""
    ...

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


class BrowserOperationReleaseResp(BaseUserMid,BaseBrowserId):
    """释放浏览器响应"""
    is_success: bool = True

__all__ = [
    # Fingerprint Params
    "BrowserFingerprintEditableParams",
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
]
