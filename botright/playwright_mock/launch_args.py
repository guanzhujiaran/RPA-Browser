"""Chromium 启动参数规范化。

浏览器启动参数由多个来源拼装（playwright driver 默认注入、botright 内置 flags、指纹参数、
UA、代理参数、调用方的 launch_arguments），同一个开关很容易被设置多次。chromium 解析命令行时
同名开关是「后者覆盖前者」（`base::CommandLine` 内部是 map），所以重复设置不只是噪音：

- 普通开关会静默互相覆盖（`--lang`、`--proxy-server`）；
- `--disable-features` / `--enable-features` 这类「逗号分隔特性列表」被后一份整条覆盖时，
  前一份特性会全部丢失。

清理策略（谁负责就只由谁设置一次）：

1. 同一份参数列表内部由 `normalize_chromium_args` 收敛：同名开关保留最后一次出现的值，
   特性列表类开关取并集；
2. 与 playwright driver 默认注入同名的开关，用 `ignore_default_args` 抑制 driver 那份，
   由我们合并后只设置一次（`build_launch_args` 返回抑制清单），这样既不会重复设置，
   也不会像「同名取最后一个」那样丢掉 driver 需要的特性；
3. headless / 代理相关的开关 driver 会自己拼，我们从自己的列表里剔除，交给 driver 唯一设置。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Final

FEATURE_LIST_SWITCHES: frozenset[str] = frozenset(
    {"disable-features", "enable-features", "disable-blink-features"}
)

# playwright chromium driver 默认注入的 feature 列表
# （见 playwright-core/src/server/chromium/chromiumSwitches.ts 的 disabledFeatures / chromiumSwitches）。
# 这里保留一份是为了在抑制 driver 那份之后，仍把它需要的特性合并进唯一的一条开关里；
# 升级 playwright/patchright 后如果这份内容与 driver 不一致，抑制会失效（driver 那份重新出现），
# 只是回到「同名开关两份、后者生效」，不会丢失功能，同步更新本常量即可。
PLAYWRIGHT_DISABLED_FEATURES: Final[tuple[str, ...]] = (
    "AvoidUnnecessaryBeforeUnloadCheckSync",
    "DestroyProfileOnBrowserClose",
    "DialMediaRouteProvider",
    "GlobalMediaControls",
    "HttpsUpgrades",
    "LensOverlay",
    "MediaRouter",
    "PaintHolding",
    "ThirdPartyStoragePartitioning",
    "BlockOriginHeaderModificationOnRedirect",
    "AvoidCorsURLLoaderRestartOnRedirect",
    "Translate",
    "AutoDeElevate",
    "OptimizationHints",
    "NetworkTimeServiceQuerying",
    "AimEnabled",
    "msForceBrowserSignIn",
    "msEdgeUpdateLaunchServicesPreferredVersion",
)
PLAYWRIGHT_ENABLED_FEATURES: Final[tuple[str, ...]] = ("CDPScreenshotNewSurface",)

# driver 自己会重复注入两次的开关（它自身的问题），同样抑制后由我们只设置一次
DRIVER_DUPLICATED_SWITCHES: Final[tuple[str, ...]] = (
    "--disable-search-engine-choice-screen",
    "--enable-unsafe-swiftshader",
)

# headless 模式下 driver 会额外注入这几个开关（见 chromium.js 的 _innerDefaultArgs），
# 自己再声明一份只会让最终命令行重复
HEADLESS_DRIVER_SWITCHES: frozenset[str] = frozenset(
    {"mute-audio", "hide-scrollbars", "blink-settings"}
)

_MISSING: Final[object] = object()


def split_switch(arg: str) -> tuple[str, str | None]:
    """把 `--name=value` / `--name` 拆成 `(name, value)`，无值时 value 为 None。"""
    name, sep, value = arg.partition("=")
    return name, value if sep else None


def merge_feature_values(values: Iterable[str | None]) -> str:
    """按出现顺序合并逗号分隔的特性列表并去重。"""
    merged: list[str] = []
    for value in values:
        for feature in (value or "").split(","):
            feature = feature.strip()
            if feature and feature not in merged:
                merged.append(feature)
    return ",".join(merged)


def normalize_chromium_args(args: Iterable[str]) -> list[str]:
    """对启动参数做去重与特性列表合并，保持开关首次出现的顺序。

    Args:
        args: 待规范化的启动参数（可含空项，空项会被丢弃）。

    Returns:
        每个开关只出现一次的参数列表；同名开关取最后一次出现的值，
        特性列表类开关取所有来源的并集。
    """
    result: list[str] = []
    positions: dict[str, int] = {}
    features: dict[str, list[str | None]] = {}

    for arg in args:
        if not arg:
            continue

        name, value = split_switch(arg)
        switch = name.lstrip("-")

        if switch in FEATURE_LIST_SWITCHES and value is not None:
            features.setdefault(switch, []).append(value)
            arg = f"{name}={merge_feature_values(features[switch])}"

        position = positions.get(switch)
        if position is None:
            positions[switch] = len(result)
            result.append(arg)
        else:
            result[position] = arg

    return result


def driver_feature_switches() -> list[str]:
    """driver 默认注入的 feature 开关（原串）。"""
    switches = [f"--disable-features={','.join(PLAYWRIGHT_DISABLED_FEATURES)}"]
    # driver 在该环境变量下不注入 --enable-features
    if not os.environ.get("PLAYWRIGHT_LEGACY_SCREENSHOT"):
        switches.append(f"--enable-features={','.join(PLAYWRIGHT_ENABLED_FEATURES)}")
    return switches


def drop_headless_driver_switches(args: Iterable[str]) -> list[str]:
    """去掉 headless 模式下 driver 会重复注入的开关。"""
    return [
        arg for arg in args if split_switch(arg)[0].lstrip("-") not in HEADLESS_DRIVER_SWITCHES
    ]


def drop_switch(args: Iterable[str], switch: str) -> list[str]:
    """去掉指定开关（`switch` 形如 `--proxy-server`），不论是否带值。"""
    return [arg for arg in args if split_switch(arg)[0] != switch]


def build_launch_args(
    flags: Iterable[str],
    *,
    headless: bool,
    use_playwright_proxy: bool,
) -> tuple[list[str], list[str]]:
    """组装最终下发的启动参数，并给出要抑制的 driver 默认开关。

    Args:
        flags: 各来源拼装出的启动参数（botright 内置 flags + 指纹参数 + UA 等）。
        headless: 是否无头启动，无头时 driver 会自己注入部分开关。
        use_playwright_proxy: 代理是否通过 launch 的 proxy 参数下发。

    Returns:
        `(args, ignore_default_args)`：前者每个开关只出现一次；
        后者用于传给 playwright，抑制 driver 重复注入的同名开关。
    """
    driver_features = dict(split_switch(switch) for switch in driver_feature_switches())
    suppressed: list[str] = [*DRIVER_DUPLICATED_SWITCHES]
    args: list[str] = []

    for arg in normalize_chromium_args(
        [*driver_feature_switches(), *DRIVER_DUPLICATED_SWITCHES, *flags]
    ):
        name, value = split_switch(arg)
        driver_value = driver_features.get(name, _MISSING)
        if driver_value is not _MISSING:
            if value == driver_value:
                # 合并结果与 driver 完全一致，交给 driver 那一份即可
                continue
            # 抑制 driver 注入的那一份（原串精确匹配），只留合并后的这一条
            suppressed.append(f"{name}={driver_value}")

        if headless and name.lstrip("-") in HEADLESS_DRIVER_SWITCHES:
            continue
        if use_playwright_proxy and name == "--proxy-server":
            continue

        args.append(arg)

    return args, ["--enable-automation", *suppressed]


__all__ = [
    "FEATURE_LIST_SWITCHES",
    "HEADLESS_DRIVER_SWITCHES",
    "PLAYWRIGHT_DISABLED_FEATURES",
    "PLAYWRIGHT_ENABLED_FEATURES",
    "build_launch_args",
    "driver_feature_switches",
    "drop_headless_driver_switches",
    "drop_switch",
    "merge_feature_values",
    "normalize_chromium_args",
    "split_switch",
]
