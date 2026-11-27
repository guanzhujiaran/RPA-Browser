"""
Core 模块 - 浏览器执行信息模型

定义浏览器执行路径等信息。
"""

import os
from typing import List
from app.config import settings
from pydantic import TypeAdapter
from sqlmodel import SQLModel
from pydantic import computed_field


# AppImage 一次性解包目录后缀，以及包内 chromium 的相对路径
APPIMAGE_EXTRACTED_SUFFIX = ".squashfs-root"
APPIMAGE_INNER_EXEC_RELATIVE_PATH = ("opt", "ungoogled-chromium", "chrome")


class BrowserExecInfoModel(SQLModel):
    browser_ua: str
    download_url: str
    full_version: str
    exec_name: str

    @computed_field
    @property
    def major_version(self) -> str:
        """主版本号（与 ``browser_ua`` 里的 ``Chrome/<major>.0.0.0`` 对齐）"""
        return self.full_version.split(".", 1)[0]

    @computed_field
    @property
    def appimage_path(self) -> str:
        """AppImage 本体路径：下载 / 校验 / 安装判断均以此为准"""
        return os.path.join(settings.chromium_executable_dir, self.exec_name)

    @computed_field
    @property
    def is_installed(self) -> bool:
        """二进制是否已落盘（AppImage 本体或解包产物）"""
        return os.path.isfile(self.appimage_path) or os.path.isfile(
            self.extracted_exec_path
        )

    @computed_field
    @property
    def extracted_appimage_dir(self) -> str:
        """AppImage 一次性解包目录（`--appimage-extract` 的产物）"""
        return self.appimage_path + APPIMAGE_EXTRACTED_SUFFIX

    @computed_field
    @property
    def extracted_exec_path(self) -> str:
        """解包后可直接执行的 chromium（不依赖 FUSE / AppRun）"""
        return os.path.join(
            self.extracted_appimage_dir, *APPIMAGE_INNER_EXEC_RELATIVE_PATH
        )

    @computed_field
    @property
    def exec_path(self) -> str:
        """启动用可执行文件路径

        已解包时优先用解包产物（多个实例共用同一份文件页），
        未解包（宿主机 / 调试环境）回落到 AppImage 本体，行为与改造前一致。
        """
        if os.path.isfile(self.extracted_exec_path):
            return self.extracted_exec_path
        return self.appimage_path


BrowserExecInfoModels = TypeAdapter(List[BrowserExecInfoModel])


__all__ = ["BrowserExecInfoModel", "BrowserExecInfoModels"]
