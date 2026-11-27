"""chromium 二进制清单（browser_exec_info.json）的读取与匹配。

该文件是本项目**唯一**的浏览器版本来源：

- ``full_version``：真实版本，决定实际启动哪个 AppImage；
- ``browser_ua``：与该二进制对应的 UA（随机生成指纹时的 UA 池）。

任何「按版本找二进制」的逻辑都必须走这里，禁止在别处写死版本号
（见 docs/浏览器真实版本与模拟版本一致性计划书.md）。
"""

import os
import random
from typing import List

import aiofiles
from loguru import logger
from pydantic import computed_field

from app.models.core.browser.exec import (
    BrowserExecInfoModel,
    BrowserExecInfoModels,
)


current_dir = os.path.dirname(os.path.realpath(__file__))


async def get_browser_exec_infos() -> List[BrowserExecInfoModel]:
    async with aiofiles.open(
        os.path.join(current_dir, "browser_exec_info.json"), mode="r"
    ) as f:
        res = await f.read()
    return BrowserExecInfoModels.validate_json(res)


class BrowserExecInfoHelper:
    browse_exec_infos: List[BrowserExecInfoModel] = []

    async def refresh(self) -> None:
        self.browse_exec_infos = await get_browser_exec_infos()

    async def list_exec_infos(self) -> List[BrowserExecInfoModel]:
        if not self.browse_exec_infos:
            await self.refresh()
        return self.browse_exec_infos

    async def get_exec_info_by_version(
        self, full_version: str | None
    ) -> BrowserExecInfoModel | None:
        """按真实版本精确匹配清单条目，找不到返回 None（不做任何回落）。"""
        version = (full_version or "").strip()
        if not version:
            return None
        for info in await self.list_exec_infos():
            if info.full_version == version:
                return info
        return None

    async def get_exec_info(
        self,
        *,
        real_version: str | None = None,
        ua: str | None = None,
    ) -> BrowserExecInfoModel:
        """按「真实版本 → UA → 第一条」的顺序定位二进制。

        真实版本是权威键；UA 匹配只用于兼容没有 ``fingerprint_real_version`` 的历史行；
        仍匹配不上才回落清单第一条，并打 WARNING（不再静默回落）。
        """
        exec_infos = await self.list_exec_infos()

        by_version = await self.get_exec_info_by_version(real_version)
        if by_version is not None:
            return by_version

        if ua:
            for info in exec_infos:
                if info.browser_ua == ua:
                    logger.info(
                        "浏览器真实版本缺失，按 UA 匹配到二进制: "
                        f"version={info.full_version}, ua={ua}"
                    )
                    return info

        fallback = exec_infos[0]
        logger.warning(
            "浏览器版本匹配失败，回落到清单第一条: "
            f"requested_version={real_version!r}, ua={ua!r}, "
            f"fallback_version={fallback.full_version}"
        )
        return fallback

    async def pick_random_exec_info(self) -> BrowserExecInfoModel:
        """从清单里随机挑一条（随机指纹的版本来源）"""
        return random.choice(await self.list_exec_infos())

    async def get_exec_info_ua_list(self) -> List[str]:
        if not self.browse_exec_infos:
            await self.refresh()
        return self.ua_list

    @computed_field
    @property
    def ua_list(self) -> List[str]:
        return [info.browser_ua for info in self.browse_exec_infos]

    @computed_field
    @property
    def full_version_list(self) -> List[str]:
        return [info.full_version for info in self.browse_exec_infos]


browser_exec_info_helper = BrowserExecInfoHelper()

__all__ = ["get_browser_exec_infos", "browser_exec_info_helper"]
