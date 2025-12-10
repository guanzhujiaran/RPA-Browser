import os
import stat

import aiofiles
import browsers
import requests
import asyncio
from app.config import settings
from app.utils.consts.browser_exe_info.browser_exec_info_utils import get_browse_exec_infos


async def download_file(url, filename):
    """Download file from URL with progress indication"""
    print(f"正在从 {url} 下载 {filename}...")
    os.makedirs('/'.join(filename.split('/')[:-1]), exist_ok=True)

    response = await asyncio.to_thread(requests.get,url=url, stream=True, proxies={'all': settings.proxy_server_url})
    response.raise_for_status()

    total_size = int(response.headers.get('content-length', 0))
    downloaded_size = 0

    async with aiofiles.open(filename, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                await file.write(chunk)
                downloaded_size += len(chunk)
                if total_size > 0:
                    percent = (downloaded_size / total_size) * 100
                    print(f"\r下载进度: {percent:.1f}%", end='', flush=True)

    print("\n下载完成!")


async def install_chromium():
    """Install ungoogled chromium browser"""
    # Check if chromium is already installed
    for exec_info in await get_browse_exec_infos():
        browser = browsers.get("chromium")
        if browser is not None or os.path.exists(exec_info.exec_path):
            print(f"Chromium 浏览器已经安装在: {browser or exec_info.exec_path}")
            return browser
        if not exec_info.exec_path:
            print(f"未找到 Chromium 浏览器可执行文件路径 {exec_info}")
            return None
        print("检测到 Chromium 浏览器未安装，开始下载...")
        # Download the AppImage
        try:
            await download_file(exec_info.download_url, exec_info.exec_path)

            # Make the AppImage executable
            st = os.stat(settings.chromium_executable_dir)
            os.chmod(settings.chromium_executable_dir, st.st_mode | stat.S_IEXEC)

            print(f"{settings.chromium_executable_dir} 已成功下载并设置为可执行文件")

            return os.path.abspath(settings.chromium_executable_dir)

        except Exception as e:
            print(f"安装过程中出现错误: {e}")
            return None


if __name__ == "__main__":
    asyncio.run(install_chromium())
