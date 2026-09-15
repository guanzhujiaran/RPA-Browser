"""虚拟显示（Xvfb）管理

RPA 浏览器必须以有头（headful）模式运行才能让目标网站认为是正常浏览器：
headless 模式会在 navigator.webdriver、屏幕/窗口尺寸、插件列表等维度被识别。
而服务器上没有物理显示器，headful 会直接失败（Missing X server or $DISPLAY），
因此这里用 Xvfb 提供一块虚拟屏幕——浏览器仍然是完整有头模式，只是画面不显示出来。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from contextlib import suppress

from loguru import logger

from app.config import settings

_xvfb_process: asyncio.subprocess.Process | None = None


def _display_socket_path(display: str) -> str:
    """Xvfb 的 unix socket 路径，如 :99 -> /tmp/.X11-unix/X99"""
    return f"/tmp/.X11-unix/X{display.lstrip(':').split('.')[0]}"


async def ensure_virtual_display() -> str | None:
    """确保进程拥有可用的 X DISPLAY，返回生效的 DISPLAY 值（失败返回 None）"""
    current_display = os.environ.get("DISPLAY")
    if not settings.xvfb_enabled:
        return current_display or None

    if not sys.platform.startswith("linux"):
        return current_display or None

    if current_display:
        logger.info(f"检测到已有显示环境，跳过 Xvfb: DISPLAY={current_display}")
        return current_display

    xvfb_executable = shutil.which("Xvfb")
    if not xvfb_executable:
        logger.warning(
            "未检测到 Xvfb，headful 浏览器将无法启动。"
            "请执行 `sudo apt install -y xvfb` 安装，或将 xvfb_enabled 设为 False 并改用 headless 模式"
        )
        return None

    display = settings.xvfb_display
    socket_path = _display_socket_path(display)
    os.makedirs(os.path.dirname(socket_path), exist_ok=True)

    # 复用已存在的同名 Xvfb（例如上次进程残留 / 手动启动的）
    if os.path.exists(socket_path):
        os.environ["DISPLAY"] = display
        logger.info(f"复用已存在的虚拟显示: DISPLAY={display}")
        return display

    global _xvfb_process
    _xvfb_process = await asyncio.create_subprocess_exec(
        xvfb_executable,
        display,
        "-screen",
        "0",
        settings.xvfb_screen,
        "-nolisten",
        "tcp",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    # 等待 Xvfb 就绪（socket 出现），避免浏览器启动时 X server 还没起来
    for _ in range(100):
        if os.path.exists(socket_path):
            break
        if _xvfb_process.returncode is not None:
            logger.error(f"Xvfb 启动失败并退出，returncode={_xvfb_process.returncode}")
            _xvfb_process = None
            return None
        await asyncio.sleep(0.1)

    os.environ["DISPLAY"] = display
    logger.info(f"已启动虚拟显示 Xvfb {display} ({settings.xvfb_screen})，DISPLAY={display}")
    return display


async def stop_virtual_display() -> None:
    """停止由本进程启动的 Xvfb"""
    global _xvfb_process
    if _xvfb_process is None:
        return
    if _xvfb_process.returncode is None:
        _xvfb_process.terminate()
        with suppress(Exception):
            await asyncio.wait_for(_xvfb_process.wait(), timeout=5)
    _xvfb_process = None


__all__ = ["ensure_virtual_display", "stop_virtual_display"]
