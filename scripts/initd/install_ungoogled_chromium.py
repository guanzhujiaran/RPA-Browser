import os
import stat

import aiofiles
import browsers
import httpx
import asyncio
from app.config import settings
from app.utils.consts.browser_exe_info.browser_exec_info_utils import get_browser_exec_infos


async def download_file(url, filename, mirror_url: str = settings.github_proxy_url):
    """Download file from URL with progress indication"""
    url = mirror_url + url
    print(f"正在从 {url} 下载 {filename}...")
    os.makedirs('/'.join(filename.split('/')[:-1]), exist_ok=True)
    async with httpx.AsyncClient() as client:
        async with client.stream('GET', url) as response:
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0

            async with aiofiles.open(filename, 'wb') as file:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    await file.write(chunk)
                    downloaded_size += len(chunk)
                    if total_size > 0:
                        percent = (downloaded_size / total_size) * 100
                        print(f"\r下载进度: {percent:.1f}%", end='', flush=True)
    print("\n下载完成!")


async def install_chromium():
    """Install ungoogled chromium browser"""
    # Check if chromium is already installed
    for exec_info in await get_browser_exec_infos():
        browser = browsers.get("chromium")
        if browser is not None or os.path.exists(exec_info.exec_path):
            print(f"Chromium 浏览器已经安装在: {browser or exec_info.exec_path}")
            # 检查并修复可执行权限
            if os.path.exists(exec_info.exec_path):
                if not os.access(exec_info.exec_path, os.X_OK):
                    print(f"检测到浏览器文件缺少可执行权限，正在修复...")
                    os.chmod(exec_info.exec_path, stat.S_IRWXU)
                    print(f"已修复可执行权限: {exec_info.exec_path}")
            continue
        if not exec_info.exec_path:
            print(f"Chromium 浏览器可执行文件路径未设置 {exec_info}")
            return None
        print("检测到 Chromium 浏览器未安装，开始下载...")
        # Download the AppImage
        await download_file(exec_info.download_url, exec_info.exec_path)

        # 设置可执行权限
        os.chmod(exec_info.exec_path, stat.S_IRWXU)
        print(f"已设置可执行权限: {exec_info.exec_path}")

        print(f"{settings.chromium_executable_dir} 已成功下载")


if __name__ == "__main__":
    asyncio.run(install_chromium())
