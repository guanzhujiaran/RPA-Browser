import os
import stat

import browsers
import requests

from app.config import settings


def download_file(url, filename):
    """Download file from URL with progress indication"""
    print(f"正在从 {url} 下载 {filename}...")
    os.makedirs('/'.join(filename.split('/')[:-1]), exist_ok=True)

    response = requests.get(url, stream=True, proxies={'all': settings.proxy_server_url})
    response.raise_for_status()

    total_size = int(response.headers.get('content-length', 0))
    downloaded_size = 0

    with open(filename, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                file.write(chunk)
                downloaded_size += len(chunk)
                if total_size > 0:
                    percent = (downloaded_size / total_size) * 100
                    print(f"\r下载进度: {percent:.1f}%", end='', flush=True)

    print("\n下载完成!")


def install_chromium():
    """Install ungoogled chromium browser"""
    # Check if chromium is already installed
    browser = browsers.get("chromium")
    if browser is not None or os.path.exists(settings.chromium_executable_path):
        print(f"Chromium 浏览器已经安装在: {browser or settings.chromium_executable_path}")
        return browser
    if not settings.chromium_executable_path:
        print(f"未找到 Chromium 浏览器可执行文件路径 {settings.chromium_executable_path}")
        return None
    print("检测到 Chromium 浏览器未安装，开始下载...")

    # GitHub release URL for ungoogled-chromium
    url = 'https://github.com/adryfish/fingerprint-chromium/releases/download/139.0.7258.154/ungoogled-chromium-139.0.7258.154-1-x86_64.AppImage'

    # Download the AppImage
    try:
        download_file(url, settings.chromium_executable_path)

        # Make the AppImage executable
        st = os.stat(settings.chromium_executable_path)
        os.chmod(settings.chromium_executable_path, st.st_mode | stat.S_IEXEC)

        print(f"{settings.chromium_executable_path} 已成功下载并设置为可执行文件")

        # Register with browsers library
        # Note: This is a simplified approach. In practice, you might need to
        # register the browser with the system or the browsers library in a more complex way.
        print("浏览器已安装完成，可以通过 ./ungoogled-chromium-139.0.7258.154-1-x86_64.AppImage 启动")

        return os.path.abspath(settings.chromium_executable_path)

    except Exception as e:
        print(f"安装过程中出现错误: {e}")
        return None


if __name__ == "__main__":
    install_chromium()
