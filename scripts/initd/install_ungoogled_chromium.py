import os
import stat

import aiofiles
import browsers
import httpx
import asyncio
from tqdm import tqdm
from app.config import settings
from app.utils.consts.browser_exe_info.browser_exec_info_utils import get_browser_exec_infos
from loguru import logger


async def download_file(url, filename, mirror_urls: list[str] = settings.github_proxy_urls):
    """Download file from URL with progress indication"""
    if not mirror_urls:
        raise ValueError("mirror_urls 不能为空")
    for mirror_url in mirror_urls:
        try:
            download_url = mirror_url + url
            logger.info(f"正在从 {download_url} 下载 {filename}...")
            os.makedirs('/'.join(filename.split('/')[:-1]), exist_ok=True)
            async with httpx.AsyncClient() as client:
                async with client.stream('GET', download_url) as response:
                    response.raise_for_status()
                    total_size = int(response.headers.get('content-length', 0))

                    with tqdm(
                        total=total_size,
                        unit="B",
                        unit_scale=True,
                        unit_divisor=1024,
                        desc=os.path.basename(filename),
                        dynamic_ncols=True,
                    ) as pbar:
                        async with aiofiles.open(filename, 'wb') as file:
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                await file.write(chunk)
                                pbar.update(len(chunk))
            logger.info("下载完成!")
            return
        except Exception as e:
            logger.error(f"下载 {filename} 失败: {e}")
            continue
    raise RuntimeError(f"所有镜像下载失败: {mirror_urls}")


async def install_chromium():
    """Install ungoogled chromium browser"""
    # Check if chromium is already installed
    for exec_info in await get_browser_exec_infos():
        browser = browsers.get("chromium")
        if browser is not None or os.path.exists(exec_info.exec_path):
            logger.info(f"Chromium 浏览器已经安装在: {browser or exec_info.exec_path}")
            # 检查并修复可执行权限
            if os.path.exists(exec_info.exec_path) and not os.access(exec_info.exec_path, os.X_OK):
                logger.info("检测到浏览器文件缺少可执行权限，正在修复...")
                os.chmod(exec_info.exec_path, stat.S_IRWXU)
                logger.info(f"已修复可执行权限: {exec_info.exec_path}")
            continue
        if not exec_info.exec_path:
            logger.info(f"Chromium 浏览器可执行文件路径未设置 {exec_info}")
            return None
        logger.info("检测到 Chromium 浏览器未安装，开始下载...")
        # Download the AppImage
        await download_file(exec_info.download_url, exec_info.exec_path)

        # 设置可执行权限
        os.chmod(exec_info.exec_path, stat.S_IRWXU)
        logger.info(f"已设置可执行权限: {exec_info.exec_path}")

        logger.info(f"{settings.chromium_executable_dir} 已成功下载")


if __name__ == "__main__":
    asyncio.run(install_chromium())
