"""启动参数去重 / 特性列表合并的单元测试。"""

from __future__ import annotations

from botright.playwright_mock.launch_args import (
    build_launch_args,
    drop_headless_driver_switches,
    drop_switch,
    driver_feature_switches,
    normalize_chromium_args,
    split_switch,
)


def switch_names(args: list[str]) -> list[str]:
    """取出参数中的开关名，用于断言是否重复。"""
    return [split_switch(arg)[0] for arg in args]


def test_normalize_keeps_single_flag() -> None:
    """同名开关只保留一份，取最后一次出现的值。"""
    args = normalize_chromium_args(
        ["--lang=en-US", "--proxy-server=127.0.0.1:1", "--lang=zh-CN"]
    )

    assert args == ["--lang=zh-CN", "--proxy-server=127.0.0.1:1"]


def test_normalize_merges_feature_lists() -> None:
    """特性列表类开关取并集，避免后来源覆盖前来源。"""
    args = normalize_chromium_args(
        [
            "--disable-features=DialMediaRouteProvider,Translate",
            "--disable-features=AudioServiceOutOfProcess,Translate",
        ]
    )

    assert args == [
        "--disable-features=DialMediaRouteProvider,Translate,AudioServiceOutOfProcess"
    ]


def test_normalize_merges_blink_features() -> None:
    """--disable-blink-features 同样合并。"""
    args = normalize_chromium_args(
        ["--disable-blink-features=AutomationControlled", "--disable-blink-features=Something"]
    )

    assert args == ["--disable-blink-features=AutomationControlled,Something"]


def test_normalize_ignores_empty_items() -> None:
    """空项被丢弃，不影响其他开关。"""
    assert normalize_chromium_args(["", "--no-pings", ""]) == ["--no-pings"]


def test_build_launch_args_merges_feature_switches_once() -> None:
    """driver 注入的 feature 开关与本地的合并成一条，driver 那份被抑制。"""
    args, ignore_default_args = build_launch_args(
        ["--disable-features=IsolateOrigins", "--no-pings"],
        headless=False,
        use_playwright_proxy=False,
    )

    names = switch_names(args)
    assert names.count("--disable-features") == 1
    assert "IsolateOrigins" in next(a for a in args if a.startswith("--disable-features"))
    # driver 注入的原串被抑制，合并后的串不在抑制列表里（否则会被一起过滤掉）
    driver_disabled = driver_feature_switches()[0]
    assert driver_disabled in ignore_default_args
    assert driver_disabled not in args


def test_build_launch_args_keeps_driver_feature_when_identical() -> None:
    """本地没有再追加特性时不重复传，交给 driver 那一份。"""
    args, ignore_default_args = build_launch_args(
        ["--no-pings"], headless=False, use_playwright_proxy=False
    )

    assert switch_names(args).count("--disable-features") == 0
    assert driver_feature_switches()[0] not in ignore_default_args


def test_build_launch_args_drops_headless_driver_switches() -> None:
    """headless 下 driver 会重复注入的开关由 driver 唯一设置。"""
    args, _ = build_launch_args(
        ["--mute-audio", "--hide-scrollbars", "--blink-settings=a=1", "--no-pings"],
        headless=True,
        use_playwright_proxy=False,
    )
    names = switch_names(args)

    assert "--no-pings" in args
    for switch in ("--mute-audio", "--hide-scrollbars", "--blink-settings"):
        assert switch not in names


def test_build_launch_args_drops_proxy_server() -> None:
    """代理由 playwright 的 proxy 参数唯一设置，参数列表里不再重复。"""
    args, _ = build_launch_args(
        ["--proxy-server=127.0.0.1:3128", "--no-pings"],
        headless=False,
        use_playwright_proxy=True,
    )

    assert "--proxy-server=127.0.0.1:3128" not in args
    assert "--no-pings" in args


def test_build_launch_args_has_no_duplicate_switch() -> None:
    """多来源拼装后每个开关只出现一次。"""
    flags = [
        "--no-pings",
        "--lang=en-US",
        "--disable-features=SomFeature",
        "--enable-features=TrustTokens",
        "--proxy-server=127.0.0.1:3128",
        "--lang=zh-CN",
        "--disable-features=IsolateOrigins",
    ]

    args, _ = build_launch_args(flags, headless=True, use_playwright_proxy=True)
    names = switch_names(args)

    assert len(names) == len(set(names))


def test_drop_headless_driver_switches() -> None:
    """headless 下 driver 会重复注入的开关被剔除，其他开关保留。"""
    args = ["--mute-audio", "--hide-scrollbars", "--blink-settings=a=1", "--no-pings"]

    assert drop_headless_driver_switches(args) == ["--no-pings"]


def test_drop_switch_removes_all_forms() -> None:
    """同名开关无论带不带值都被移除。"""
    args = ["--proxy-server=127.0.0.1:1", "--lang=zh-CN", "--proxy-server"]

    assert drop_switch(args, "--proxy-server") == ["--lang=zh-CN"]
