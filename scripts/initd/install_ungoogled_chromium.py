import os
import stat
from contextlib import suppress

import aiofiles
import httpx
import asyncio
from tqdm import tqdm
from app.config import settings
from app.utils.consts.browser_exe_info.browser_exec_info_utils import (
    get_browser_exec_infos,
)
from loguru import logger

# fingerprint-chromium 的 AppImage 实际体积均在 150MB 以上，
# 小于该阈值一律视为下载中断/被代理返回错误页面后的残缺文件
MIN_APPIMAGE_SIZE = 50 * 1024 * 1024
# 下载中的临时文件后缀：只有校验通过后才原子替换成正式文件，
# 避免进程被中断(Ctrl+C / 服务停止)时留下"看起来已安装"的残缺文件
PART_SUFFIX = ".part"


def safe_remove(path: str) -> None:
    """删除文件，不存在或删除失败均不抛异常"""
    if path and os.path.exists(path):
        with suppress(OSError):
            os.remove(path)


def cleanup_download_artifacts(directory: str | None) -> None:
    """清理历史遗留的半成品下载文件(.part)"""
    if not directory or not os.path.isdir(directory):
        return
    for name in os.listdir(directory):
        if name.endswith(PART_SUFFIX):
            part_path = os.path.join(directory, name)
            safe_remove(part_path)
            logger.info(f"已清理残留的半成品下载文件: {part_path}")


def is_valid_appimage(path: str) -> bool:
    """校验 AppImage 是否为完整可用的二进制文件

    GitHub 代理经常在传输中途断流，留下大小正常的残缺 ELF，
    直接启动会 SIGSEGV（playwright 报 TargetClosedError）。
    这里同时校验：体积 + ELF magic + AppImage type2 magic("AI\\x02")。
    """
    if not os.path.isfile(path):
        return False
    if os.path.getsize(path) < MIN_APPIMAGE_SIZE:
        return False
    with open(path, "rb") as f:
        header = f.read(16)
    return header[:4] == b"\x7fELF" and header[8:11] == b"AI\x02"


def normalize_mirror_urls(mirror_urls: list[str | None] | None) -> list[str | None]:
    """归一化代理镜像列表

    - 为 None 时取配置 settings.github_proxy_urls
    - 为空列表时表示不使用代理，直连原始 URL
    - 列表元素为 None 同样表示该轮直连（可与代理混用）
    """
    if mirror_urls is None:
        mirror_urls = settings.github_proxy_urls
    return list(mirror_urls) if mirror_urls else [None]


async def download_file(
    url,
    filename,
    mirror_urls: list[str | None] | None = None,
    progress_position: int | None = None,
):
    """Download file from URL with progress indication

    Args:
        url: 目标文件 URL(相对路径时会由 mirror_url 补齐前缀；直连时需自带 http(s):// 前缀)
        filename: 落盘路径
        mirror_urls: 代理镜像列表，仍然串行逐个尝试（一个镜像失败才换下一个）；
                     为空表示不使用代理直连，默认为 settings.github_proxy_urls
        progress_position: tqdm 进度条行号，多个文件并发下载时需要区分避免互相覆盖
    """
    mirror_urls = normalize_mirror_urls(mirror_urls)
    # 先写 .part 临时文件，校验通过后再原子替换成正式文件：
    # 任何中断(失败/被取消)都只会残留 .part，不会污染正式文件
    part_file = filename + PART_SUFFIX
    for mirror_url in mirror_urls:
        try:
            download_url = (mirror_url or "") + url
            logger.info(f"正在从 {download_url} 下载 {filename}...")
            os.makedirs("/".join(filename.split("/")[:-1]), exist_ok=True)
            safe_remove(part_file)
            # httpx 默认 read 超时只有 5s，慢速代理下极易 ReadTimeout 导致下载中断
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
            ) as client:
                async with client.stream("GET", download_url) as response:
                    response.raise_for_status()
                    total_size = int(response.headers.get("content-length", 0))

                    with tqdm(
                        total=total_size,
                        unit="B",
                        unit_scale=True,
                        unit_divisor=1024,
                        desc=os.path.basename(filename),
                        dynamic_ncols=True,
                        position=progress_position,
                    ) as pbar:
                        downloaded_size = 0
                        async with aiofiles.open(part_file, "wb") as file:
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                await file.write(chunk)
                                downloaded_size += len(chunk)
                                pbar.update(len(chunk))
            # 传输中断会产生体积不足的残缺文件，必须视为失败并切换到下一个镜像
            if total_size and downloaded_size != total_size:
                raise IOError(f"下载不完整: {downloaded_size}/{total_size} bytes")
            if not is_valid_appimage(part_file):
                raise IOError(
                    f"下载产物校验失败(非完整 AppImage): {os.path.getsize(part_file)} bytes"
                )
            os.replace(part_file, filename)
            logger.info("下载完成!")
            return
        except asyncio.CancelledError:
            # CancelledError 是 BaseException，只捕获 Exception 会漏掉 Ctrl+C / 服务停止的场景，
            # 那时半成品文件会残留下来并被下次启动误判为“已安装”
            logger.warning(f"下载被中断，清理半成品文件: {part_file}")
            safe_remove(part_file)
            raise
        except Exception as e:
            logger.error(f"下载 {download_url}\n 失败: {e}")
            # 清理残缺文件，避免下次启动时被误判为“已安装”
            safe_remove(part_file)
            continue
    raise RuntimeError(f"所有镜像下载失败(含直连): {mirror_urls or '直连'}")


async def install_one_executable(exec_info, progress_position: int | None = None) -> None:
    """校验并安装单个浏览器镜像（并发单元）

    镜像列表内部仍串行：只有当前代理失败时才换下一个代理，不会对同一个文件并发抢多个代理。
    """
    # 只在目标 AppImage 完整可用时才跳过下载:
    # 1) 不能因为系统装了普通 chromium 就跳过 fingerprint-chromium
    # 2) 不能因为文件存在就跳过,残缺文件会直接导致浏览器 SIGSEGV
    if is_valid_appimage(exec_info.exec_path):
        logger.info(f"Chromium 浏览器已经安装在: {exec_info.exec_path}")
        # 检查并修复可执行权限
        if not os.access(exec_info.exec_path, os.X_OK):
            logger.info("检测到浏览器文件缺少可执行权限，正在修复...")
            os.chmod(exec_info.exec_path, stat.S_IRWXU)
            logger.info(f"已修复可执行权限: {exec_info.exec_path}")
        return
    if os.path.exists(exec_info.exec_path):
        logger.warning(
            f"检测到损坏/不完整的浏览器镜像({os.path.getsize(exec_info.exec_path)} bytes)，删除后重新下载: "
            f"{exec_info.exec_path}"
        )
        safe_remove(exec_info.exec_path)
    logger.info(f"检测到 Chromium 浏览器未安装，开始下载: {os.path.basename(exec_info.exec_path)}")
    await download_file(
        exec_info.download_url,
        exec_info.exec_path,
        progress_position=progress_position,
    )
    # 设置可执行权限
    os.chmod(exec_info.exec_path, stat.S_IRWXU)
    logger.info(f"已设置可执行权限: {exec_info.exec_path}")


async def install_chromium():
    """Install ungoogled chromium browser

    多个镜像文件之间并发下载，单个文件内部串行尝试代理镜像。
    """
    # 清理上次进程被中断时残留的 .part 半成品
    cleanup_download_artifacts(settings.chromium_executable_dir)

    pending = []
    for exec_info in await get_browser_exec_infos():
        if not exec_info.exec_path:
            logger.info(f"Chromium 浏览器可执行文件路径未设置 {exec_info}")
            continue
        pending.append(exec_info)

    if not pending:
        return

    logger.info(f"开始并发下载 {len(pending)} 个浏览器镜像...")
    results = await asyncio.gather(
        *(
            install_one_executable(exec_info, progress_position=index)
            for index, exec_info in enumerate(pending)
        ),
        return_exceptions=True,
    )

    failed = []
    for exec_info, result in zip(pending, results):
        if isinstance(result, BaseException):
            logger.error(
                f"浏览器镜像安装失败 {os.path.basename(exec_info.exec_path)}: {result}"
            )
            failed.append(exec_info.exec_path)
    if failed:
        raise RuntimeError(f"{len(failed)} 个浏览器镜像安装失败: {failed}")

    logger.info(f"{settings.chromium_executable_dir} 已成功下载")


if __name__ == "__main__":
    asyncio.run(install_chromium())
