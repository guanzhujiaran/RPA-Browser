"""浏览器版本校验与同步。

真实版本（``fingerprint_real_version``）只能来自 ``browser_exec_info.json`` 清单，
它决定实际启动的 chromium 二进制；模拟版本（``fingerprint_brand_version``）是下发给
``--fingerprint-brand-version`` 的报告值。

两者约束是「**主版本必须一致，patch 可不同**」：

- 上游 ``--fingerprint-brand-version`` 只影响声明层（UA-CH 的 ``brands[].version`` = major，
  ``Full-Version-List`` / ``getHighEntropyValues().uaFullVersion`` = 完整四段号），
  不改 major 时内核特性集 / TLS 指纹都不变，改 patch 安全且能避免整批浏览器报同一个 patch；
- 跨 major 会缺 API、TLS/JA3 对不上，必被识破，一律拒绝。

本模块是「版本三件套（真实版本 / 模拟版本 / UA）」保持一致性的唯一入口：
- ``resolve_exec_info``：入参校验 + 归一（真实版本白名单 + 模拟版本格式/主版本）；
- ``apply_exec_info``：把指纹对象上的版本、UA、指纹字典一次性改写为解析结果。

见 docs/浏览器真实版本与模拟版本一致性计划书.md。
"""

import re

from sqlmodel import SQLModel

from app.models.common.exceptions.base_exception import InvalidParamException
from app.models.core.browser.exec import BrowserExecInfoModel
from app.models.core.browser.fingerprint import (
    BaseFingerprintBrowserInitParams,
    BrowserEnum,
)
from app.utils.consts.browser_exe_info.browser_exec_info_utils import (
    browser_exec_info_helper,
)


#: UA-CH 里带真实版本号的品牌名（``Not;A=Brand`` 这类噪声品牌保持原值）
_VERSIONED_BRANDS = frozenset(
    {"chromium", "google chrome", "microsoft edge", "opera", "vivaldi"}
)

#: 版本号格式：1~4 段纯数字。1 段（只给 major）按「主版本」解释，其余按完整版本号
_VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+){0,3}$")


class ResolvedBrowserVersion(SQLModel):
    """版本入参的解析结果：选哪个二进制 + 最终写库的模拟版本。"""

    exec_info: BrowserExecInfoModel
    #: 模拟版本（完整四段号）；用户只给主版本号时补成该条目的 ``full_version``
    brand_version: str


def _available_versions_hint(exec_infos: list[BrowserExecInfoModel]) -> str:
    return " / ".join(info.full_version for info in exec_infos)


def _normalize_brand_version(brand: str) -> str:
    """校验模拟版本格式（1~4 段纯数字）。"""
    if not _VERSION_PATTERN.match(brand):
        raise InvalidParamException(
            f"模拟版本格式非法: {brand}（需为 x.y.z.w 四段数字，如 150.0.7871.124）"
        )
    return brand


def _resolve_brand_of(exec_info: BrowserExecInfoModel, brand: str) -> str:
    """把模拟版本补成完整四段号（只给了主版本时取该条目的 full_version）。"""
    return exec_info.full_version if "." not in brand else brand


async def resolve_exec_info(
    real_version: str | None,
    brand_version: str | None,
) -> ResolvedBrowserVersion | None:
    """校验并归一版本入参，返回「二进制条目 + 模拟版本」。

    - 两者都未提供 → ``None``（不改动版本，保持现状）；
    - 提供真实版本 → 必须在清单内（精确匹配）；模拟版本若同时提供，格式必须是
      ``x.y.z.w`` 且主版本与真实版本相同（patch 允许不同），否则 400；
    - 只提供模拟版本 → 先按完整版本匹配；不中再按「主版本号唯一匹配」，
      命中后把真实版本补成同一条目。

    Raises:
        InvalidParamException: 真实版本不在清单内、模拟版本格式非法或主版本冲突
    """
    real = (real_version or "").strip()
    brand = (brand_version or "").strip()
    if not real and not brand:
        return None

    exec_infos = await browser_exec_info_helper.list_exec_infos()
    hint = _available_versions_hint(exec_infos)

    if real:
        exec_info = await browser_exec_info_helper.get_exec_info_by_version(real)
        if exec_info is None:
            raise InvalidParamException(
                f"不支持的真实浏览器版本: {real}，可选值: {hint}"
            )
        if not brand:
            return ResolvedBrowserVersion(
                exec_info=exec_info, brand_version=exec_info.full_version
            )

        brand = _normalize_brand_version(brand)
        if brand.split(".", 1)[0] != exec_info.major_version:
            raise InvalidParamException(
                f"模拟版本 {brand} 与真实版本 {exec_info.full_version} 主版本不一致，"
                f"模拟版本只能在同一主版本内调整 patch"
            )
        return ResolvedBrowserVersion(
            exec_info=exec_info, brand_version=_resolve_brand_of(exec_info, brand)
        )

    brand = _normalize_brand_version(brand)
    exec_info = await browser_exec_info_helper.get_exec_info_by_version(brand)
    if exec_info is None:
        brand_major = brand.split(".", 1)[0]
        same_major = [info for info in exec_infos if info.major_version == brand_major]
        if len(same_major) != 1:
            raise InvalidParamException(f"不支持的浏览器版本: {brand}，可选值: {hint}")
        exec_info = same_major[0]

    return ResolvedBrowserVersion(
        exec_info=exec_info, brand_version=_resolve_brand_of(exec_info, brand)
    )


def sync_ua_ch_data(
    ua_data: dict,
    exec_info: BrowserExecInfoModel,
    brand_version: str | None = None,
) -> dict:
    """把用户代理客户端提示（UA-CH）里的版本号改成模拟版本。

    ``brands[].version`` 取主版本、``uaFullVersion`` / ``fullVersionList[].version`` 取完整四段号；
    ``Not;A=Brand`` 这类噪声品牌保持原值，只改真实存在的浏览器品牌。
    不传 ``brand_version`` 时等于真实版本（随机生成的场景）。
    """
    full_version = (brand_version or "").strip() or exec_info.full_version
    major_version = full_version.split(".", 1)[0]
    synced = {**ua_data, "uaFullVersion": full_version}

    brands = synced.get("brands")
    if isinstance(brands, list):
        synced["brands"] = [
            (
                {**brand, "version": major_version}
                if isinstance(brand, dict)
                and str(brand.get("brand", "")).lower() in _VERSIONED_BRANDS
                else brand
            )
            for brand in brands
        ]

    full_version_list = synced.get("fullVersionList")
    if isinstance(full_version_list, list):
        synced["fullVersionList"] = [
            (
                {**brand, "version": full_version}
                if isinstance(brand, dict)
                and str(brand.get("brand", "")).lower() in _VERSIONED_BRANDS
                else brand
            )
            for brand in full_version_list
        ]

    return synced


def _sync_fingerprint_dict(
    target: BaseFingerprintBrowserInitParams,
    exec_info: BrowserExecInfoModel,
    brand_version: str,
) -> None:
    """同步 ``patchright_fingerprint_dict`` 里的 UA / UA-CH 版本。

    整体重新赋值新 dict：``patchright_fingerprint_dict`` 是 JSON 列，
    原地修改不会被 SQLAlchemy 判定为脏数据。
    """
    fingerprint_dict = target.patchright_fingerprint_dict
    if not isinstance(fingerprint_dict, dict):
        return
    navigator = fingerprint_dict.get("navigator")
    if not isinstance(navigator, dict):
        return

    synced_navigator = {**navigator, "userAgent": exec_info.browser_ua}
    ua_data = synced_navigator.get("userAgentData")
    if isinstance(ua_data, dict):
        synced_navigator["userAgentData"] = sync_ua_ch_data(
            ua_data, exec_info, brand_version
        )

    target.patchright_fingerprint_dict = {
        **fingerprint_dict,
        "navigator": synced_navigator,
    }


def apply_exec_info(
    target: BaseFingerprintBrowserInitParams,
    exec_info: BrowserExecInfoModel,
    brand_version: str | None = None,
) -> None:
    """把解析结果应用到指纹对象。

    - 真实版本 / UA：来自清单条目（决定二进制）；
    - 模拟版本：优先用入参（同主版本的其它 patch），未传则等于真实版本；
    - UA-CH 字典：完整版本号跟随模拟版本，主版本与真实版本一致。
    """
    resolved_brand = (brand_version or "").strip() or exec_info.full_version
    target.fingerprint_real_version = exec_info.full_version
    target.fingerprint_brand_version = resolved_brand
    target.patchright_browser_ua = exec_info.browser_ua
    # 模拟版本与浏览器品牌必须成对出现（见模型的 check_browser_and_brand_version_consistency）；
    # 清单里的二进制都是 Chromium，未指定品牌时按 Chrome 补齐。
    if target.fingerprint_browser is None:
        target.fingerprint_browser = BrowserEnum.chrome
    _sync_fingerprint_dict(target, exec_info, resolved_brand)


async def apply_versions(
    target: BaseFingerprintBrowserInitParams,
    real_version: str | None,
    brand_version: str | None,
) -> bool:
    """校验入参版本并同步到指纹对象；两者都未提供时不做任何改动。

    Returns:
        bool: 是否发生了版本同步
    """
    resolved = await resolve_exec_info(real_version, brand_version)
    if resolved is None:
        return False
    apply_exec_info(target, resolved.exec_info, resolved.brand_version)
    return True


__all__ = [
    "ResolvedBrowserVersion",
    "resolve_exec_info",
    "apply_exec_info",
    "apply_versions",
    "sync_ua_ch_data",
]
