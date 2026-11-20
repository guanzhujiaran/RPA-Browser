import os
import shutil
import stat
import tempfile
from contextlib import suppress

import aiofiles
import httpx
import asyncio
from tqdm import tqdm
from app.config import settings
from app.models.core.browser.exec import (
    APPIMAGE_EXTRACTED_SUFFIX,
    APPIMAGE_INNER_EXEC_RELATIVE_PATH,
    BrowserExecInfoModel,
)
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
# 解包用的临时目录前缀（位于 chrome 目录内，保证与正式目录同一文件系统可原子 rename）
EXTRACT_TMP_PREFIX = ".extract-"


def safe_remove(path: str) -> None:
    """删除文件，不存在或删除失败均不抛异常"""
    if path and os.path.exists(path):
        with suppress(OSError):
            os.remove(path)


def cleanup_download_artifacts(directory: str | None) -> None:
    """清理历史遗留的半成品（.part 下载文件 / .extract-* 临时解包目录）"""
    if not directory or not os.path.isdir(directory):
        return
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if name.endswith(PART_SUFFIX):
            safe_remove(path)
            logger.info(f"已清理残留的半成品下载文件: {path}")
        elif name.startswith(EXTRACT_TMP_PREFIX) and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            logger.info(f"已清理残留的临时解包目录: {path}")


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


def has_chrome(root_dir: str) -> bool:
    """root_dir 下是否含 AppImage 包内的 chromium 可执行文件"""
    return os.path.isfile(os.path.join(root_dir, *APPIMAGE_INNER_EXEC_RELATIVE_PATH))


def is_extracted_appimage(appimage_path: str) -> bool:
    """AppImage 是否已经完整解包（以包内 chromium 是否存在为准）"""
    return has_chrome(appimage_path + APPIMAGE_EXTRACTED_SUFFIX)


def can_run_appimage_directly() -> bool:
    """当前环境能否直接执行 AppImage

    type2 AppImage 依赖 FUSE 挂载：容器内通常既没有 /dev/fuse 也没有
    fusermount，直接执行会报 "No suitable fusermount binary found on the $PATH"。
    """
    if not os.path.exists("/dev/fuse"):
        return False
    return bool(shutil.which("fusermount") or shutil.which("fusermount3"))


# 同一版本的解包串行化：并发创建会话时避免重复解包、互删目录
_extract_locks: dict[str, asyncio.Lock] = {}


def _get_extract_lock(appimage_path: str) -> asyncio.Lock:
    lock = _extract_locks.get(appimage_path)
    if lock is None:
        lock = asyncio.Lock()
        _extract_locks[appimage_path] = lock
    return lock


async def extract_appimage(exec_info: BrowserExecInfoModel) -> bool:
    """把 AppImage 一次性解包到 `<appimage>.squashfs-root`（幂等、可重入）

    在 chrome 目录内建临时目录执行 `--appimage-extract`，校验产物完整后再
    原子 rename 成正式目录；任何中断都只会残留 `.extract-*` 临时目录（下次启动
    清理），不会留下"看起来已安装"的半成品。

    失败只记录 ERROR 并返回 False，由调用方回落到直接执行 AppImage。
    """
    appimage_path = exec_info.appimage_path
    target_dir = appimage_path + APPIMAGE_EXTRACTED_SUFFIX
    if is_extracted_appimage(appimage_path):
        return True

    async with _get_extract_lock(appimage_path):
        # 等锁期间可能已被其它协程解包完成
        if is_extracted_appimage(appimage_path):
            return True
        if not is_valid_appimage(appimage_path):
            logger.error(f"AppImage 不完整，跳过解包: {appimage_path}")
            return False

        chrome_dir = os.path.dirname(appimage_path)
        os.makedirs(chrome_dir, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(prefix=EXTRACT_TMP_PREFIX, dir=chrome_dir)
        try:
            logger.info(
                f"开始解包 AppImage（一次性解包，之后各实例共享同一份文件页）: {appimage_path}"
            )
            process = await asyncio.create_subprocess_exec(
                # cwd 已切到临时目录，这里必须用绝对路径
                os.path.abspath(appimage_path),
                "--appimage-extract",
                cwd=tmp_dir,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                tail = (stderr or b"").decode("utf-8", "replace").strip()[-500:]
                raise RuntimeError(
                    f"解包进程退出码 {process.returncode}: {tail or '无 stderr 输出'}"
                )

            extracted_root = os.path.join(tmp_dir, "squashfs-root")
            if not has_chrome(extracted_root):
                raise RuntimeError(
                    "解包产物缺少 " + "/".join(APPIMAGE_INNER_EXEC_RELATIVE_PATH)
                )
            chrome_path = os.path.join(
                extracted_root, *APPIMAGE_INNER_EXEC_RELATIVE_PATH
            )
            if not os.access(chrome_path, os.X_OK):
                os.chmod(chrome_path, stat.S_IRWXU)

            # 旧目录可能是历史半成品/残包：先删再原子替换
            if os.path.isdir(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
            os.replace(extracted_root, target_dir)
            logger.info(f"AppImage 解包完成: {target_dir}")
            return True
        except asyncio.CancelledError:
            logger.warning(f"解包被中断，清理临时目录: {tmp_dir}")
            raise
        except Exception as e:  # noqa: BLE001 - 解包失败不阻塞启动，运行时回落 AppImage
            logger.error(f"AppImage 解包失败（将回落直接执行 AppImage）: {e}")
            return False
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def ensure_appimage_extracted(exec_info: BrowserExecInfoModel) -> str:
    """返回可用于启动浏览器的可执行文件路径，必要时先解包 AppImage

    - 已解包：直接用解包产物（不依赖 FUSE，多实例共用同一份文件页）
    - 未解包但当前环境能直接执行 AppImage（宿主机有 FUSE）：保持原有行为，
      不额外占磁盘，宿主机 / VS Code 调试路径不回归
    - 否则在首次真正使用时解包；失败则回落 AppImage 本体（日志已记录 ERROR）
    """
    if is_extracted_appimage(exec_info.appimage_path):
        return exec_info.exec_path
    if can_run_appimage_directly():
        return exec_info.appimage_path
    await extract_appimage(exec_info)
    return exec_info.exec_path


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


async def install_one_executable(
    exec_info, progress_position: int | None = None
) -> None:
    """校验并安装单个浏览器镜像（并发单元）

    镜像列表内部仍串行：只有当前代理失败时才换下一个代理，不会对同一个文件并发抢多个代理。
    """
    # 只在目标 AppImage 完整可用时才跳过下载:
    # 1) 不能因为系统装了普通 chromium 就跳过 fingerprint-chromium
    # 2) 不能因为文件存在就跳过,残缺文件会直接导致浏览器 SIGSEGV
    # 注意：这里必须用 appimage_path（AppImage 本体），不能用 exec_path——
    # 后者在已解包时会指向 squashfs-root 内的 chrome，会把 AppImage 误判为未安装。
    if is_valid_appimage(exec_info.appimage_path):
        logger.info(f"Chromium 浏览器已经安装在: {exec_info.appimage_path}")
        # 检查并修复可执行权限
        if not os.access(exec_info.appimage_path, os.X_OK):
            logger.info("检测到浏览器文件缺少可执行权限，正在修复...")
            os.chmod(exec_info.appimage_path, stat.S_IRWXU)
            logger.info(f"已修复可执行权限: {exec_info.appimage_path}")
        # 已安装版本不在这里解包：4~5 个版本全解包会多吃数 GB 磁盘，
        # 改为「首次真正使用时解包」（见 ensure_appimage_extracted 的调用方）。
        return
    if os.path.exists(exec_info.appimage_path):
        logger.warning(
            f"检测到损坏/不完整的浏览器镜像({os.path.getsize(exec_info.appimage_path)} bytes)，删除后重新下载: "
            f"{exec_info.appimage_path}"
        )
        safe_remove(exec_info.appimage_path)
    logger.info(
        f"检测到 Chromium 浏览器未安装，开始下载: {os.path.basename(exec_info.appimage_path)}"
    )
    await download_file(
        exec_info.download_url,
        exec_info.appimage_path,
        progress_position=progress_position,
    )
    # 设置可执行权限
    os.chmod(exec_info.appimage_path, stat.S_IRWXU)
    logger.info(f"已设置可执行权限: {exec_info.appimage_path}")
    # 新下载的镜像大概率马上要用：无 FUSE 的环境（生产容器）顺手解包，
    # 失败只记录 ERROR，不阻塞服务启动（运行时回落 AppImage）
    await ensure_appimage_extracted(exec_info)


async def install_chromium():
    """Install ungoogled chromium browser

    多个镜像文件之间并发下载，单个文件内部串行尝试代理镜像。
    """
    # 清理上次进程被中断时残留的 .part 半成品
    cleanup_download_artifacts(settings.chromium_executable_dir)

    pending = []
    for exec_info in await get_browser_exec_infos():
        if not exec_info.appimage_path:
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
                f"浏览器镜像安装失败 {os.path.basename(exec_info.appimage_path)}: {result}"
            )
            failed.append(exec_info.appimage_path)
    if failed:
        raise RuntimeError(f"{len(failed)} 个浏览器镜像安装失败: {failed}")

    logger.info(f"{settings.chromium_executable_dir} 已成功下载")


if __name__ == "__main__":
    asyncio.run(install_chromium())
