"""后台会话闲置回收的「运行模式」门控。

背景：`cleanup_all_sessions` 原先只在 `RUNNING_MODE == PROD` 时执行，
导致 DEV / 本地跑完工作流后浏览器会话永远不回收（只能手动关）。
现改为由 `browser_session_cleanup_all_modes`（默认 True）控制，
PROD 语义保持不变。

覆盖点：
1. DEV + 默认开关 → 执行回收；
2. DEV + 显式关闭开关 → 不执行（可退回旧行为）；
3. PROD + 关闭开关 → 仍执行（PROD 恒执行）。
"""

import pytest

from app.config import ConfigRunningModeEnum, settings
from app.services.RPA_browser import background_tasks as bg_module
from app.services.RPA_browser.background_tasks import BackgroundTasks


class _CleanupRecorder:
    """记录 `_check_session_cleanup` 被调用的次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1


@pytest.fixture()
def recorder(monkeypatch: pytest.MonkeyPatch) -> _CleanupRecorder:
    """替换 live_service 的清理入口，避免真的去回收会话。"""
    rec = _CleanupRecorder()
    monkeypatch.setattr(bg_module.live_service, "_check_session_cleanup", rec)
    return rec


@pytest.fixture()
def running_mode(monkeypatch: pytest.MonkeyPatch):
    """设置运行模式与「所有模式都回收」开关。"""

    def _set(mode: ConfigRunningModeEnum, all_modes: bool) -> None:
        monkeypatch.setattr(settings, "RUNNING_MODE", mode)
        monkeypatch.setattr(settings, "browser_session_cleanup_all_modes", all_modes)

    return _set


@pytest.mark.asyncio
async def test_dev_cleanup_enabled_by_default(
    recorder: _CleanupRecorder, running_mode
) -> None:
    """DEV 下默认开关为 True → 空闲回收照常执行。"""
    running_mode(ConfigRunningModeEnum.DEV, True)

    await BackgroundTasks.cleanup_all_sessions()

    assert recorder.calls == 1


@pytest.mark.asyncio
async def test_dev_cleanup_can_be_disabled(
    recorder: _CleanupRecorder, running_mode
) -> None:
    """DEV 下显式关闭开关 → 退回旧行为（不回收）。"""
    running_mode(ConfigRunningModeEnum.DEV, False)

    await BackgroundTasks.cleanup_all_sessions()

    assert recorder.calls == 0


@pytest.mark.asyncio
async def test_prod_cleanup_always_runs(
    recorder: _CleanupRecorder, running_mode
) -> None:
    """PROD 恒执行，不受新开关影响。"""
    running_mode(ConfigRunningModeEnum.PROD, False)

    await BackgroundTasks.cleanup_all_sessions()

    assert recorder.calls == 1
